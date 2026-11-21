"""refresh and verify, end to end and offline: a bump on a branch gets the checksums it needs."""

import gzip
import unittest

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
        self.assertIn("pkg: sha256", out)
        self.assertIn("/dists/s/main/binary-amd64/Packages", out, "the apt sum names its index")
        self.commit("sums")
        code, out, err = self.cli("refresh", "--base", "main")
        self.assertEqual((code, out), (0, ""), "a second run rewrote again")
        self.assertIn("already right", err)

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

    def index(self, *stanzas: tuple[str, str]) -> None:
        text = "\n".join(
            f"Package: pkg\nVersion: {v}\nFilename: pool/pkg_{v}_amd64.deb\nSHA256: {s}\n" for v, s in stanzas
        )
        (self.root / "apt/dists/s/main/binary-amd64/Packages.gz").write_bytes(gzip.compress(text.encode()))

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
