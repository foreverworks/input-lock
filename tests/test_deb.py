"""A vendor's Packages index: where it is and what it says about one .deb."""

import unittest

from input_lock.deb import Published, find, index_urls, same_file

INDEX = """Package: tool
Version: 1.0.0
Filename: pool/main/t/tool/tool_1.0.0_amd64.deb
SHA256: AAAA
Description: a tool
 over two lines
 Version: 9.9.9

Package: tool
Version: 2:1.1.0~rc1
Filename: pool/main/t/tool/tool_1.1.0%7erc1_amd64.deb
SHA256: bbbb
SHA512: cccc
"""


class Deb(unittest.TestCase):
    def test_index_urls(self) -> None:
        self.assertEqual(
            index_urls("https://example.com/debian/?suite=trixie&components=stable,test&binaryArch=arm64"),
            [
                "https://example.com/debian/dists/trixie/stable/binary-arm64/Packages",
                "https://example.com/debian/dists/trixie/test/binary-arm64/Packages",
            ],
        )
        self.assertTrue(index_urls("https://example.com?suite=s&components=main")[0].endswith("/binary-amd64/Packages"))

    def test_find(self) -> None:
        self.assertEqual(
            find(INDEX, "tool", "1.0.0"), Published("pool/main/t/tool/tool_1.0.0_amd64.deb", {"sha256": "aaaa"})
        )
        found = find(INDEX, "tool", "2:1.1.0~rc1")
        self.assertIsNotNone(found)
        assert found is not None
        self.assertEqual(found.checksums, {"sha256": "bbbb", "sha512": "cccc"})
        self.assertIsNone(find(INDEX, "tool", "9.9.9"), "a continuation line is not a field")
        self.assertIsNone(find(INDEX, "other", "1.0.0"))

    def test_same_file(self) -> None:
        published = Published("pool/main/t/tool/tool_1.1.0%7erc1_amd64.deb", {})
        entry = {"version": "1.1.0~rc1", "url": "https://example.com/pool/tool_{version}_amd64.deb"}
        self.assertTrue(same_file(entry, published))
        self.assertFalse(same_file({**entry, "url": "https://example.com/pool/other_{version}_amd64.deb"}, published))
        self.assertFalse(same_file(entry, Published("", {})))


if __name__ == "__main__":
    unittest.main()
