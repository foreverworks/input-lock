"""Offline fixtures: a throwaway git repository, file:// downloads and a vendor's apt repository."""

import contextlib
import gzip
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from input_lock import cli
from input_lock import lock as lock_module


def sha(data: bytes, algorithm: str = "sha256") -> str:
    return hashlib.new(algorithm, data).hexdigest()


class Workspace(unittest.TestCase):
    """A git repository holding lock.json on main, downloads under dl/, and an apt repository under apt/."""

    def setUp(self) -> None:
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)
        widened = mock.patch.object(lock_module, "URL_SCHEMES", ("https://", "file://"))
        widened.start()
        self.addCleanup(widened.stop)
        self.files = {
            "tool-1.0.0.bin": b"one",
            "tool-1.1.0.bin": b"two",
            "legacy-1.bin": b"old",
            "legacy-2.bin": b"new",
        }
        (self.root / "dl").mkdir()
        for name, data in self.files.items():
            (self.root / "dl" / name).write_bytes(data)
        self.debs = {"1.0.0": b"deb-one", "1.1.0": b"deb-two"}
        pool = self.root / "apt" / "pool"
        pool.mkdir(parents=True)
        stanzas = []
        for version, data in self.debs.items():
            (pool / f"pkg_{version}_amd64.deb").write_bytes(data)
            stanzas.append(
                f"Package: pkg\nVersion: {version}\nFilename: pool/pkg_{version}_amd64.deb\nSHA256: {sha(data)}\n"
            )
        index = self.root / "apt" / "dists" / "s" / "main" / "binary-amd64"
        index.mkdir(parents=True)
        (index / "Packages.gz").write_bytes(gzip.compress("\n".join(stanzas).encode()))  # only the .gz: the fallback
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.write(self.base_lock())
        self.git("add", "lock.json")
        self.commit("base")

    def url(self, path: str) -> str:
        return (self.root / path).as_uri()

    def base_lock(self) -> dict[str, object]:
        dl = self.url("dl")
        return {
            "schema": 1,
            "inputs": {
                "tool": {
                    "version": "1.0.0",
                    "datasource": "github-releases",
                    "depName": "example/tool",
                    "url": dl + "/tool-{version}.bin",
                    "sha256": sha(b"one"),
                    "sha1": sha(b"one", "sha1"),
                },
                "pkg": {
                    "apt": "pkg",
                    "version": "1.0.0",
                    "datasource": "deb",
                    "depName": "pkg",
                    "registryUrl": self.url("apt") + "?suite=s&components=main&binaryArch=amd64",
                    "url": self.url("apt") + "/pool/pkg_{version}_amd64.deb",
                    "sha256": sha(b"deb-one"),
                },
                "legacy": {"version": "1", "url": dl + "/legacy-{version}.bin", "sha1": sha(b"old", "sha1")},
                "same": {"version": "1.0.0", "url": dl + "/tool-1.0.0.bin", "sha256": sha(b"one")},
            },
        }

    @property
    def lock_path(self) -> Path:
        return self.repo / "lock.json"

    def write(self, data: object) -> None:
        self.lock_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def read(self) -> dict[str, dict[str, dict[str, object]]]:
        data: dict[str, dict[str, dict[str, object]]] = json.loads(self.lock_path.read_text(encoding="utf-8"))
        return data

    def bump(self, **versions: str) -> None:
        data = self.read()
        for name, version in versions.items():
            data["inputs"][name]["version"] = version
        self.write(data)

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True, text=True, check=True)
        return done.stdout

    def commit(self, message: str) -> None:
        self.git("-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qam", message)

    def cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([*args, "--lock", str(self.lock_path)])
        return code, out.getvalue(), err.getvalue()
