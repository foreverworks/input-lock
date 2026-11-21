"""The network edge refuses what it cannot vouch for: a cut download, a downgrade, a broken index."""

import lzma
import socket
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from input_lock import fetch


def serve_once(response: bytes) -> str:
    """A one-request HTTP server on localhost that answers `response` and hangs up; its URL."""
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def answer() -> None:
        connection, _ = server.accept()
        with connection, server:
            connection.recv(65536)
            connection.sendall(response)

    threading.Thread(target=answer, daemon=True).start()
    return f"http://127.0.0.1:{port}/file"


class Fetch(unittest.TestCase):
    def test_a_cut_download_is_never_hashed(self) -> None:
        url = serve_once(b"HTTP/1.0 200 OK\r\nContent-Length: 1000\r\n\r\n0123456789")
        with self.assertRaises(ValueError) as caught:
            fetch.hashes(url, ["sha256"])
        self.assertIn("10 bytes arrived of the 1000 announced", str(caught.exception))

    def test_a_whole_download_is(self) -> None:
        url = serve_once(b"HTTP/1.0 200 OK\r\nContent-Length: 3\r\n\r\none")
        self.assertEqual(
            fetch.hashes(url, ["sha256"])["sha256"], "7692c3ad3540bb803c020b3aee66cd8887123234ea0c6e7143c0add73ff431ed"
        )

    def test_no_downgrade(self) -> None:
        fetch.no_downgrade("https://e.com/a", "https://cdn.e.com/a")
        fetch.no_downgrade("file:///a", "file:///a")
        with self.assertRaises(ValueError):
            fetch.no_downgrade("https://e.com/a", "http://e.com/a")

    def test_a_broken_index_is_a_value_error(self) -> None:
        with TemporaryDirectory() as held:
            index = Path(held) / "Packages"
            index.with_suffix(".xz").write_bytes(lzma.compress(b"Package: x\n")[:-8])
            with self.assertRaises(ValueError) as caught:
                fetch.read_index(index.as_uri())
            self.assertIn("not a readable index", str(caught.exception))
            self.assertIsNone(fetch.read_index((Path(held) / "nothing").as_uri()))


if __name__ == "__main__":
    unittest.main()
