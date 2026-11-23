"""refresh and verify, end to end and offline: a bump on a branch gets the checksums it needs."""

import gzip
import json
import subprocess
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

from input_lock import cli, fetch
from input_lock.lock import dump, loads

from .support import Workspace, sha


class Refresh(Workspace):
    def test_nothing_moved_nothing_written(self) -> None:
        before = self.lock_path.read_text()
        code, out, _ = self.cli("refresh", "--base", "main")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(self.lock_path.read_text(), before)

    def test_a_bump_gets_its_checksums(self) -> None:
        self.git("checkout", "-qb", "renovate/tool")
        self.bump(tool="1.1.0", pkg="1.1.0", legacy="2")
        self.commit("bump")
        code, out, err = self.cli("refresh", "--base", "main")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out.splitlines()), 5, out)  # tool sha1+sha256, pkg sha256, legacy sha1 and a new sha256
        text = self.lock_path.read_text()
        self.assertEqual(dump(loads(text)), text, "the lock lost its own form")
        inputs = self.read()["inputs"]
        self.assertEqual(inputs["tool"]["sha256"], sha(b"two"))
        self.assertEqual(inputs["tool"]["sha1"], sha(b"two", "sha1"))
        self.assertEqual(inputs["pkg"]["sha256"], sha(b"deb-two"), "an apt entry takes its index's sum")
        self.assertEqual(inputs["legacy"]["sha1"], sha(b"new", "sha1"))
        self.assertEqual(inputs["legacy"]["sha256"], sha(b"new"), "a moved entry always gains a sha256")
        self.assertEqual(inputs["same"]["sha256"], sha(b"one"), "an entry that did not move changed")
        self.assertIn(f"pkg: sha256 {sha(b'deb-one')} -> {sha(b'deb-two')} (from ", out, "old value, new value")
        self.assertIn(f"legacy: sha256 (none) -> {sha(b'new')}", out, "a checksum the entry did not have")
        self.assertIn("/dists/s/main/binary-amd64/Packages", out, "the apt sum names its index")
        self.commit("sums")
        code, out, err = self.cli("refresh", "--base", "main")
        self.assertEqual((code, out), (0, ""), "a second run rewrote again")
        self.assertIn("already right", err)

    def test_the_snapshots_per_archive_are_kept(self) -> None:
        snapshots = {"debian": "20261123T084014Z", "debian-security": "20261123T141315Z"}
        lock = json.loads(self.lock_path.read_text())
        self.lock_path.write_text(dump({**lock, "debian_snapshots": snapshots, "schema": 3}))
        self.bump(tool="1.1.0")
        code, _, err = self.cli("refresh", "--entry", "tool")
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(self.lock_path.read_text())["debian_snapshots"], snapshots)

    def test_a_version_the_index_lacks_fails_and_writes_nothing(self) -> None:
        self.git("checkout", "-qb", "renovate/pkg")
        self.bump(tool="1.1.0", pkg="9.9.9")
        self.commit("bump to nothing")
        before = self.lock_path.read_text()
        code, _, err = self.cli("refresh", "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("pkg 9.9.9 is not in", err)
        self.assertIn("nothing written", err)
        self.assertEqual(self.lock_path.read_text(), before)

    def test_a_digest_that_is_not_hex_is_refused_and_writes_nothing(self) -> None:
        self.git("checkout", "-qb", "renovate/pkg")
        self.bump(pkg="1.1.0")
        self.commit("bump")
        self.index(("1.0.0", sha(b"deb-one")), ("1.1.0", "not-a-hex-digest"))
        before = self.lock_path.read_text()
        code, out, err = self.cli("refresh", "--base", "main")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("error    pkg:", err)
        self.assertIn("'not-a-hex-digest'", err)
        self.assertIn("nothing written", err)
        self.assertEqual(self.lock_path.read_text(), before)

    def test_a_digest_of_the_wrong_length_or_case_is_refused(self) -> None:
        self.bump(pkg="1.1.0")
        for bad in (sha(b"deb-two")[:-1], "a" * 63 + "g"):
            with self.subTest(bad=bad):
                self.index(("1.1.0", bad))
                before = self.lock_path.read_text()
                code, _, err = self.cli("refresh", "--entry", "pkg")
                self.assertEqual(code, 1, err)
                self.assertIn("is not a hex digest", err)
                self.assertEqual(self.lock_path.read_text(), before)

    def test_a_lock_that_would_not_check_is_never_written(self) -> None:
        self.bump(tool="1.1.0")
        before = self.lock_path.read_text()
        with mock.patch.object(fetch, "checksums_for", return_value=({"sha256": "not-hex"}, "somewhere")):
            code, out, err = self.cli("refresh", "--entry", "tool")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("inputs.tool.sha256 is not 64 lowercase hex digits", err)
        self.assertEqual(self.lock_path.read_text(), before)

    def test_a_lock_not_in_its_own_form_is_refused_until_formatted(self) -> None:
        data = self.read()
        data["inputs"]["same"]["sha256"] = "0" * 64
        self.lock_path.write_text(json.dumps(data), encoding="utf-8")  # one line: valid, not canonical
        before = self.lock_path.read_text()
        code, out, err = self.cli("refresh", "--entry", "same")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("run `input-lock format` first", err)
        self.assertEqual(self.lock_path.read_text(), before)
        self.assertEqual(self.cli("format")[0], 0)
        code, out, err = self.cli("refresh", "--entry", "same")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["inputs"]["same"]["sha256"], sha(b"one"))
        self.assertEqual(len(out.splitlines()), 1, "the commit holds the checksum and no reformatting")

    def test_a_git_failure_is_not_a_lock_that_is_new(self) -> None:
        self.bump(tool="1.1.0")
        real = cli.git

        def failing(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[0] == "show":
                return subprocess.CompletedProcess(args, 128, "", "fatal: bad object")
            return real(args, cwd)

        before = self.lock_path.read_text()
        with mock.patch.object(cli, "git", failing):
            code, out, err = self.cli("refresh", "--base", "main")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("git show", err)
        self.assertIn("fatal: bad object", err)
        self.assertEqual(self.lock_path.read_text(), before)

    def test_a_missing_download_fails(self) -> None:
        self.bump(tool="7.0.0")
        code, _, err = self.cli("refresh", "--entry", "tool")
        self.assertEqual(code, 1)
        self.assertIn("error    tool:", err)

    def test_named_entries_without_git(self) -> None:
        data = self.read()
        data["inputs"]["same"]["sha256"] = "0" * 64
        self.write(data)
        code, out, _ = self.cli("refresh", "--entry", "same")
        self.assertEqual(code, 0)
        self.assertEqual(self.read()["inputs"]["same"]["sha256"], sha(b"one"))
        self.assertIn("same: sha256", out)
        self.assertEqual(self.cli("refresh", "--entry", "nope")[0], 1)
        self.assertEqual(self.cli("refresh")[0], 1, "refresh needs --base or --entry")

    def test_no_common_ancestor_says_why(self) -> None:
        self.git("checkout", "-q", "--orphan", "elsewhere")
        self.commit("unrelated")
        self.git("checkout", "-q", "main")
        code, _, err = self.cli("refresh", "--base", "elsewhere")
        self.assertEqual(code, 1)
        self.assertIn("fetch-depth: 0", err)

    def test_a_lock_new_on_the_branch(self) -> None:
        self.git("checkout", "-qb", "add-lock")
        self.git("rm", "-q", "--cached", "lock.json")
        self.commit("no lock yet")
        self.git("checkout", "-qb", "later")
        self.git("add", "lock.json")
        self.commit("the lock arrives")
        code, out, _ = self.cli("refresh", "--base", "add-lock")
        self.assertEqual(code, 0)
        self.assertIn("legacy: sha256", out, "every entry is new, so every entry is checked")


class Verify(Workspace):
    def test_verify(self) -> None:
        code, out, _ = self.cli("verify")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("ok "), 5)
        data = self.read()
        data["inputs"]["tool"]["sha1"] = "0" * 40
        self.write(data)
        code, out, _ = self.cli("verify", "--entry", "tool")
        self.assertEqual(code, 1)
        self.assertIn("mismatch tool: sha1", out)

    def test_an_index_that_dropped_the_version_is_a_note(self) -> None:
        self.index(("1.1.0", sha(b"deb-two")))
        code, out, _ = self.cli("verify", "--entry", "pkg")
        self.assertEqual(code, 0, out)
        self.assertIn("note     pkg: its index no longer lists this version", out)

    def test_an_index_that_disagrees_with_the_download(self) -> None:
        self.index(("1.0.0", "f" * 64))
        code, out, _ = self.cli("verify", "--entry", "pkg")
        self.assertEqual(code, 1, out)
        self.assertIn("says sha256 " + "f" * 64, out)

    def test_an_index_whose_file_is_another(self) -> None:
        text = f"Package: pkg\nVersion: 1.0.0\nFilename: pool/other_1.0.0_amd64.deb\nSHA256: {sha(b'deb-one')}\n"
        (self.root / "apt/dists/s/main/binary-amd64/Packages.gz").write_bytes(gzip.compress(text.encode()))
        code, out, _ = self.cli("verify", "--entry", "pkg")
        self.assertEqual(code, 1, out)
        self.assertIn("the index's file is pool/other_1.0.0_amd64.deb", out)


if __name__ == "__main__":
    unittest.main()
