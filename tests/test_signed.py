"""What a vendor signed, read strictly: gpgv's verdict, one clear-signed message, armor, sums lists, a Release."""

import base64
import datetime
import unittest

from input_lock.deb import dists_url, index_urls, release_fields, release_sums, suite_of
from input_lock.signed import clear_signed_problem, dearmor, listed, status_problem, sums_name

SHA = "a" * 64
NOW = datetime.datetime(2026, 11, 22, tzinfo=datetime.UTC)
GOOD_STATUS = "[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG 1234 Vendor\n[GNUPG:] VALIDSIG ABCD 2026-11-11 0 4 0 22 8 01 ABCD\n"
CLEAR = (
    b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA256\n\ntext\n"
    b"-----BEGIN PGP SIGNATURE-----\n\nxx\n-----END PGP SIGNATURE-----\n"
)
RELEASE = f"""Origin: vendor
Suite: stable
Codename: trixie
Valid-Until: Sat, 28 Nov 2026 00:00:00 UTC
SHA256:
 {SHA} 10 main/binary-amd64/Packages
 {"B" * 64} 5 main/binary-amd64/Packages.gz
"""


class Status(unittest.TestCase):
    def test_a_valid_signature_passes(self) -> None:
        self.assertIsNone(status_problem(0, GOOD_STATUS))

    def test_anything_else_fails(self) -> None:
        cases = {
            "exit 2": (2, GOOD_STATUS),
            "no VALIDSIG": (0, "[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG 1234 Vendor\n"),
            "nothing at all": (0, ""),
            "a bad one beside a good one": (0, GOOD_STATUS + "[GNUPG:] BADSIG 99 Other\n"),
            "an expired key": (0, GOOD_STATUS + "[GNUPG:] EXPKEYSIG 1234 Vendor\n"),
            "a revoked key": (0, GOOD_STATUS + "[GNUPG:] REVKEYSIG 1234 Vendor\n"),
            "no public key": (2, "[GNUPG:] ERRSIG 1234 1 8 01 0 9\n[GNUPG:] NO_PUBKEY 1234\n"),
        }
        for case, (code, status) in cases.items():
            with self.subTest(case=case):
                self.assertIsNotNone(status_problem(code, status))

    def test_human_text_cannot_pose_as_status(self) -> None:
        self.assertIsNotNone(status_problem(0, "gpgv: [GNUPG:] VALIDSIG forged\n"))


class ClearSigned(unittest.TestCase):
    def test_one_message_passes(self) -> None:
        self.assertIsNone(clear_signed_problem(CLEAR))
        self.assertIsNone(clear_signed_problem(b"\n" + CLEAR + b"\n\n"))

    def test_text_around_it_or_a_second_message_fails(self) -> None:
        for case, data in {
            "before": b"evil  file\n" + CLEAR,
            "after": CLEAR + b"evil  file\n",
            "two messages": CLEAR + CLEAR,
            "not clear-signed": b"just text\n",
        }.items():
            with self.subTest(case=case):
                self.assertIsNotNone(clear_signed_problem(data))


class Dearmor(unittest.TestCase):
    def test_binary_passes_through_and_armor_unwraps(self) -> None:
        key = bytes(range(256)) * 3
        self.assertEqual(dearmor(key), key)
        lines = [base64.b64encode(key)[i : i + 64] for i in range(0, len(base64.b64encode(key)), 64)]
        armored = (
            b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nComment: a vendor's key\n\n"
            + b"\n".join(lines)
            + b"\n=abcd\n-----END PGP PUBLIC KEY BLOCK-----\n"
        )
        self.assertEqual(dearmor(armored), key)
        self.assertEqual(dearmor(armored + armored), key + key, "every block, in order")

    def test_broken_armor_fails(self) -> None:
        with self.assertRaises(ValueError):
            dearmor(b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nnot base64!\n-----END PGP PUBLIC KEY BLOCK-----\n")
        with self.assertRaises(ValueError):
            dearmor(b"-----BEGIN PGP MESSAGE-----\n\nAAAA\n-----END PGP MESSAGE-----\n")


class Sums(unittest.TestCase):
    def test_the_name_is_the_path_under_the_lists_folder(self) -> None:
        sums = "https://nodejs.org/dist/v26.13.1/SHASUMS256.txt.asc"
        self.assertEqual(
            sums_name("https://nodejs.org/dist/v26.13.1/node-v26.13.1.tar.xz", sums), "node-v26.13.1.tar.xz"
        )
        self.assertEqual(sums_name("https://nodejs.org/dist/v26.13.1/win-x64/node.exe", sums), "win-x64/node.exe")
        self.assertEqual(sums_name("https://cdn.example.com/a/tool%2Bx.tgz", sums), "tool+x.tgz")

    def test_listed(self) -> None:
        text = f"{SHA}  tool.tgz\n{'b' * 64} *other.tgz\n{'C' * 128}  ./tool.tgz\n{'d' * 64}  sub/tool.tgz\r\n"
        self.assertEqual(listed(text, "tool.tgz"), {"sha256": SHA, "sha512": "c" * 128})
        self.assertEqual(listed(text, "other.tgz"), {"sha256": "b" * 64})
        self.assertEqual(listed(text, "sub/tool.tgz"), {"sha256": "d" * 64})

    def test_listed_refuses(self) -> None:
        for case, (text, name) in {
            "not named": (f"{SHA}  tool.tgz\n", "other.tgz"),
            "two sums of one algorithm": (f"{SHA}  tool.tgz\n{'b' * 64}  tool.tgz\n", "tool.tgz"),
            "a length that is no sha": ("abcd  tool.tgz\n", "tool.tgz"),
        }.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                listed(text, name)


class Release(unittest.TestCase):
    def test_where_the_suite_is(self) -> None:
        registry = "https://apt.example.com/debian/?suite=stable&components=main,extra&binaryArch=arm64"
        self.assertEqual(suite_of(registry), "stable")
        self.assertEqual(dists_url(registry), "https://apt.example.com/debian/dists/stable")
        self.assertEqual(
            index_urls(registry),
            [
                "https://apt.example.com/debian/dists/stable/main/binary-arm64/Packages",
                "https://apt.example.com/debian/dists/stable/extra/binary-arm64/Packages",
            ],
        )

    def test_its_sums(self) -> None:
        self.assertEqual(release_fields(RELEASE)["Suite"], "stable")
        want = {"main/binary-amd64/Packages": SHA, "main/binary-amd64/Packages.gz": "b" * 64}
        self.assertEqual(release_sums(RELEASE, "stable", NOW), want)
        self.assertEqual(release_sums(RELEASE, "trixie", NOW), want, "the codename names the suite too")

    def test_another_suite_an_expired_one_or_none_fails(self) -> None:
        later = NOW + datetime.timedelta(days=30)
        for case, (text, suite, now) in {
            "another suite": (RELEASE, "unstable", NOW),
            "past Valid-Until": (RELEASE, "stable", later),
            "a Valid-Until that is no date": (RELEASE.replace("Sat, 28 Nov 2026 00:00:00 UTC", "soon"), "stable", NOW),
            "no SHA256": ("Suite: stable\nMD5Sum:\n 00 1 main/binary-amd64/Packages\n", "stable", NOW),
        }.items():
            with self.subTest(case=case), self.assertRaises(ValueError):
                release_sums(text, suite, now)


if __name__ == "__main__":
    unittest.main()
