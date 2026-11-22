"""The command line, as a consumer runs it: bin/input-lock from a checkout, isolated mode."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from input_lock import __version__
from input_lock.identity import input_identity
from input_lock.lock import build_env, parse, shell

from . import ROOT
from .support import Workspace, sha
from .test_lock import EXAMPLE_TEXT

EXAMPLE = str(ROOT / "examples" / "lock.json")


def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(ROOT / "bin" / "input-lock"), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


class Cli(unittest.TestCase):
    def test_check_format_get(self) -> None:
        self.assertEqual(run("check", "--lock", EXAMPLE).returncode, 0)
        self.assertEqual(run("check", "--lock", EXAMPLE, "--image", "builder", "--image", "runner").returncode, 0)
        refused = run("check", "--lock", EXAMPLE, "--image", "builder")
        self.assertEqual(refused.returncode, 1)
        self.assertIn("lock.json: inputs.alloy.used_by names ['runner']", refused.stderr)
        self.assertEqual(run("format", "--check", "--lock", EXAMPLE).returncode, 0)
        self.assertEqual(
            run("get", "jq", "--lock", EXAMPLE).stdout,
            "https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64\n",
        )
        self.assertEqual(run("get", "jq", "version", "--lock", EXAMPLE).stdout, "1.8.2\n")
        self.assertEqual(run("get", "jq", "used_by", "--lock", EXAMPLE).stdout, "builder runner\n")
        self.assertEqual(run("get", "alloy", "major", "--lock", EXAMPLE).stdout, "1\n")
        self.assertEqual(run("get", "jq", "nothing", "--lock", EXAMPLE).returncode, 1)
        self.assertEqual(run("get", "nobody", "--lock", EXAMPLE).returncode, 1)

    def test_env_and_identity_match_the_library(self) -> None:
        lock = parse(EXAMPLE_TEXT)
        self.assertEqual(
            run("env", "--image", "builder", "--lock", "-", stdin=EXAMPLE_TEXT).stdout,
            shell(build_env(lock, "builder")),
        )
        snapshot = EXAMPLE_TEXT.replace('"inputs": {', '"debian_snapshot": "20261121T005332Z",\n  "inputs": {', 1)
        self.assertIn("LOCK_DEBIAN_SNAPSHOT=", run("env", "--lock", "-", stdin=snapshot).stdout)
        self.assertNotIn("LOCK_DEBIAN_SNAPSHOT=", run("env", "--no-snapshot", "--lock", "-", stdin=snapshot).stdout)
        self.assertEqual(run("identity", "--lock", EXAMPLE).stdout.strip(), input_identity([], lock["inputs"]))
        self.assertEqual(
            run("identity", "--image", "builder", "--lock", EXAMPLE).stdout,
            run("identity", "--entry", "shellcheck", "--entry", "jq", "--lock", EXAMPLE).stdout,
        )

    def test_refresh_needs_a_file_and_identity_one_selection(self) -> None:
        refused = run("refresh", "--entry", "jq", "--lock", "-", stdin=EXAMPLE_TEXT)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("give it a file", refused.stderr)
        self.assertEqual(run("identity", "--image", "builder", "--entry", "jq", "--lock", EXAMPLE).returncode, 2)

    def test_a_future_schema(self) -> None:
        done = run("check", "--lock", "-", stdin='{"schema": 3, "inputs": {}}')
        self.assertEqual(done.returncode, 1)
        self.assertIn("upgrade input-lock", done.stderr)

    def test_format_rewrites(self) -> None:
        with tempfile.TemporaryDirectory() as held:
            path = Path(held) / "lock.json"
            path.write_text(json.dumps(json.loads(EXAMPLE_TEXT)), encoding="utf-8")
            self.assertEqual(run("format", "--check", "--lock", str(path)).returncode, 1)
            self.assertEqual(run("format", "--lock", str(path)).returncode, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), EXAMPLE_TEXT)

    def test_sh_and_version(self) -> None:
        self.assertIn("lock_fetch()", run("sh").stdout)
        self.assertEqual(run("--version").stdout.strip(), f"input-lock {__version__}")


@unittest.skipUnless(
    shutil.which("bash") and shutil.which("curl") and shutil.which("sha1sum"), "needs bash, curl, coreutils"
)
class Shell(Workspace):
    """lock.sh, sourced after a build.env, downloads exactly what the lock pins."""

    def bash(self, script: str) -> subprocess.CompletedProcess[str]:
        lock = parse(self.lock_path.read_text(encoding="utf-8"))
        (self.root / "build.env").write_text(shell(build_env(lock)), encoding="utf-8")
        helpers = ROOT / "src" / "input_lock" / "lock.sh"
        prelude = f"set -euo pipefail; . '{self.root}/build.env'; . '{helpers}'; cd '{self.root}'\n"
        return subprocess.run(["bash", "-c", prelude + script], capture_output=True, text=True, check=False)

    def test_fetch_verifies(self) -> None:
        done = self.bash("lock_fetch tool got.bin && lock_fetch legacy legacy.bin && lock_var tool version")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout, "1.0.0\n")
        self.assertEqual(sha((self.root / "got.bin").read_bytes()), sha(b"one"))

    def test_a_wrong_file_fails(self) -> None:
        (self.root / "dl" / "tool-1.0.0.bin").write_bytes(b"tampered")
        done = self.bash("lock_fetch tool got.bin")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("is not the file the lock pins", done.stderr)
        self.assertEqual(sorted(p.name for p in self.root.glob("got.bin*")), [], "an unverified file was left behind")

    def test_an_unknown_entry_fails_naming_it(self) -> None:
        done = self.bash("lock_var nobody url")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("LOCK_NOBODY_URL is not set", done.stderr)


if __name__ == "__main__":
    unittest.main()
