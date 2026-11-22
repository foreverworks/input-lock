"""signed_by, end to end and offline: a throwaway vendor key signs an apt Release and a checksum list.

GnuPG makes the keys and signatures here; input-lock itself only ever runs gpgv. Skipped where
gpg or gpgv is not installed (Debian and Ubuntu: gnupg, gpgv).
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from input_lock import fetch
from input_lock.lock import problems

from .support import Workspace, sha

HAVE_GNUPG = shutil.which("gpg") is not None and shutil.which("gpgv") is not None


def gpg(home: Path, *args: str, data: bytes = b"") -> bytes:
    done = subprocess.run(
        ["gpg", "--homedir", str(home), "--batch", "--yes", "--pinentry-mode", "loopback", "--passphrase", "", *args],
        input=data,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return done.stdout


@unittest.skipUnless(HAVE_GNUPG, "gpg and gpgv are not installed")
class Signed(Workspace):
    home: ClassVar[Path]
    held: ClassVar[tempfile.TemporaryDirectory[str]]

    @classmethod
    def setUpClass(cls) -> None:
        cls.held = tempfile.TemporaryDirectory(prefix="il-gpg-")
        cls.home = Path(cls.held.name)
        cls.home.chmod(0o700)
        for who in ("vendor", "stranger"):
            gpg(cls.home, "--quick-gen-key", f"{who} <{who}@example.com>", "ed25519", "sign", "never")

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(["gpgconf", "--homedir", str(cls.home), "--kill", "all"], check=False, capture_output=True)
        cls.held.cleanup()

    def setUp(self) -> None:
        super().setUp()
        dl = self.root / "dl"
        (dl / "keys.gpg").write_bytes(gpg(self.home, "--export", "vendor@example.com"))
        (dl / "keys.asc").write_bytes(gpg(self.home, "--armor", "--export", "vendor@example.com"))
        (dl / "stranger.gpg").write_bytes(gpg(self.home, "--export", "stranger@example.com"))
        self.sums({"tool-1.0.0.bin": sha(b"one"), "tool-1.1.0.bin": sha(b"two")})
        self.release()
        data = self.read()
        data["inputs"]["keys"] = {"version": "1", "url": self.url("dl/keys.gpg"), "sha256": self.digest("keys.gpg")}
        data["inputs"]["pkg"]["signed_by"] = "keys"
        data["inputs"]["tool"]["signed_by"] = "keys"
        data["inputs"]["tool"]["sums_url"] = self.url("dl") + "/SHA256SUMS.asc"
        self.write(data)
        self.commit("signed")
        self.assertEqual(problems(json.loads(self.lock_path.read_text())), [])

    def digest(self, name: str) -> str:
        return sha((self.root / "dl" / name).read_bytes())

    def sign(self, data: bytes, *how: str, who: str = "vendor") -> bytes:
        return gpg(self.home, "--local-user", f"{who}@example.com", *how, "--output", "-", data=data)

    def sums(self, files: dict[str, str], who: str = "vendor") -> None:
        text = "".join(f"{value}  {name}\n" for name, value in files.items()).encode()
        dl = self.root / "dl"
        (dl / "SHA256SUMS").write_bytes(text)
        (dl / "SHA256SUMS.asc").write_bytes(self.sign(text, "--clearsign", who=who))
        (dl / "SHA256SUMS.sig").write_bytes(self.sign(text, "--detach-sign", who=who))

    def release(self, suite: str = "s", detached: bool = False) -> None:
        dists = self.root / "apt" / "dists" / "s"
        index = (dists / "main" / "binary-amd64" / "Packages.gz").read_bytes()
        text = f"Suite: {suite}\nSHA256:\n {sha(index)} {len(index)} main/binary-amd64/Packages.gz\n".encode()
        if detached:
            (dists / "Release").write_bytes(text)
            (dists / "Release.gpg").write_bytes(self.sign(text, "--detach-sign"))
            (dists / "InRelease").unlink(missing_ok=True)
        else:
            (dists / "InRelease").write_bytes(self.sign(text, "--clearsign"))

    def set_entry(self, name: str, **fields: str) -> None:
        data = self.read()
        data["inputs"][name].update(fields)
        for field in [field for field, value in fields.items() if value == ""]:
            del data["inputs"][name][field]
        self.write(data)

    def refreshed(self, *names: str) -> tuple[int, str, str]:
        return self.cli("refresh", *[arg for name in names for arg in ("--entry", name)])

    def assert_refused(self, needle: str, *names: str) -> None:
        before = self.lock_path.read_text()
        code, out, err = self.refreshed(*names)
        self.assertEqual(code, 1, out + err)
        self.assertIn(needle, err)
        self.assertIn("nothing written", err)
        self.assertEqual(self.lock_path.read_text(), before)

    # ---- what passes ---------------------------------------------------------------------------

    def test_a_signed_bump_gets_its_checksums(self) -> None:
        self.git("checkout", "-qb", "renovate/tool")
        self.bump(tool="1.1.0", pkg="1.1.0")
        self.commit("bump")
        code, out, err = self.cli("refresh", "--base", "main")
        self.assertEqual(code, 0, err)
        inputs = self.read()["inputs"]
        self.assertEqual(inputs["tool"]["sha256"], sha(b"two"))
        self.assertEqual(inputs["tool"]["sha1"], sha(b"two", "sha1"), "an algorithm the list lacks is downloaded")
        self.assertEqual(inputs["pkg"]["sha256"], sha(b"deb-two"))
        self.assertIn("SHA256SUMS.asc)", out, "the tool's checksum names the signed list")

    def test_no_download_when_the_list_has_it(self) -> None:
        self.set_entry("tool", sha1="")
        self.commit("sha256 only")
        self.bump(tool="1.1.0")
        with mock.patch.object(fetch, "hashes", side_effect=AssertionError("downloaded")):
            code, _, err = self.refreshed("tool")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["inputs"]["tool"]["sha256"], sha(b"two"))

    def test_a_detached_signature_an_armored_keyring_and_a_release_gpg(self) -> None:
        self.set_entry("keys", url=self.url("dl/keys.asc"), sha256=self.digest("keys.asc"))
        self.set_entry("tool", sums_url=self.url("dl/SHA256SUMS"), sums_sig_url=self.url("dl/SHA256SUMS.sig"))
        self.release(detached=True)
        self.bump(tool="1.1.0", pkg="1.1.0")
        code, _, err = self.refreshed("tool", "pkg")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read()["inputs"]["pkg"]["sha256"], sha(b"deb-two"))

    def test_verify_checks_the_signature_and_the_list(self) -> None:
        code, out, err = self.cli("verify", "--entry", "tool", "--entry", "pkg")
        self.assertEqual(code, 0, out + err)
        self.assertIn("ok       tool: signature of", out)
        self.assertIn("ok       pkg: signature of", out)
        self.sums({"tool-1.0.0.bin": sha(b"evil")})
        code, out, _ = self.cli("verify", "--entry", "tool")
        self.assertEqual(code, 1)
        self.assertIn(f"mismatch tool: {self.url('dl')}/SHA256SUMS.asc says sha256 {sha(b'evil')}", out)

    # ---- what fails, writing nothing -------------------------------------------------------------

    def test_an_index_the_release_does_not_vouch_for(self) -> None:
        self.bump(pkg="1.1.0")
        self.index(("1.1.0", sha(b"evil")))  # a new index, the signed Release still lists the old one's sha256
        self.assert_refused("the signed Release lists", "pkg")

    def test_a_release_for_another_suite(self) -> None:
        self.release(suite="other")
        self.bump(pkg="1.1.0")
        self.assert_refused("not 's'", "pkg")

    def test_a_signature_by_another_key(self) -> None:
        self.sums({"tool-1.1.0.bin": sha(b"two")}, who="stranger")
        self.bump(tool="1.1.0")
        self.assert_refused("the signature does not verify", "tool")
        self.set_entry("keys", url=self.url("dl/stranger.gpg"), sha256=self.digest("stranger.gpg"))
        self.bump(pkg="1.1.0")
        self.assert_refused("the signature does not verify", "pkg")

    def test_a_changed_signed_list(self) -> None:
        self.bump(tool="1.1.0")
        listed = self.root / "dl" / "SHA256SUMS.asc"
        listed.write_bytes(listed.read_bytes().replace(sha(b"two").encode(), sha(b"evil").encode()))
        self.assert_refused("BADSIG", "tool")

    def test_a_line_outside_the_signed_message(self) -> None:
        self.bump(tool="1.1.0")
        listed = self.root / "dl" / "SHA256SUMS.asc"
        evil = f"{sha(b'evil')}  tool-1.1.0.bin\n".encode()
        listed.write_bytes(evil + listed.read_bytes())
        self.assert_refused("not a clear-signed message", "tool")
        listed.write_bytes(listed.read_bytes()[len(evil) :] + evil)
        self.assert_refused("something follows the signature", "tool")

    def test_a_list_that_does_not_name_the_file(self) -> None:
        self.sums({"tool-1.0.0.bin": sha(b"one")})
        self.bump(tool="1.1.0")
        self.assert_refused("does not name tool-1.1.0.bin", "tool")

    def test_a_keyring_that_moved_without_its_checksum(self) -> None:
        (self.root / "dl" / "keys.gpg").write_bytes(gpg(self.home, "--export"))  # both keys now
        self.bump(tool="1.1.0")
        self.assert_refused("does not match its sha256 in the lock", "tool")

    def test_no_gpgv_fails_closed(self) -> None:
        self.bump(tool="1.1.0", pkg="1.1.0")
        with mock.patch("shutil.which", return_value=None):
            self.assert_refused("gpgv is not installed", "tool", "pkg")
            code, out, _ = self.cli("verify", "--entry", "tool")
        self.assertEqual(code, 1)
        self.assertIn("error    tool: gpgv is not installed", out)

    def test_the_library_needs_the_keyring(self) -> None:
        entry = self.read()["inputs"]["tool"]
        with self.assertRaises(ValueError) as caught:
            fetch.checksums_for(entry)  # type: ignore[arg-type]
        self.assertIn("no keyring was given", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
