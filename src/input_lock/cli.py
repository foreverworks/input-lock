"""input-lock: the command line. Reading files, git and the network happen here and in `fetch`."""

import argparse
import subprocess
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from . import __version__, fetch
from .identity import input_identity
from .lock import Entry, Lock, LockError, build_env, dump, entries_for, loads, parse, placeholders, render_url, shell
from .refresh import apply, describe, moved

DEFAULT_LOCK = "lock.json"


def read(path: str) -> str:
    """The lock's text; '-' reads standard input."""
    return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")


def read_lock(path: str, images: Sequence[str] | None = None) -> Lock:
    return parse(read(path), images or None)


def git(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)


def file_at(rev: str, path: str) -> str:
    """The lock's text at a git revision (its merge base with HEAD), '' when the file was not there yet."""
    where = Path(path).resolve()
    base = git(["merge-base", rev, "HEAD"], where.parent)
    if base.returncode != 0:
        raise LockError(f"git merge-base {rev} HEAD: {base.stderr.strip()}")
    shown = git(["show", f"{base.stdout.strip()}:./{where.name}"], where.parent)
    return shown.stdout if shown.returncode == 0 else ""


def selected(lock: Lock, image: str | None, names: Sequence[str]) -> dict[str, Entry]:
    """The entries an image uses, or the named ones, or all of them."""
    if names:
        unknown = sorted(set(names) - set(lock["inputs"]))
        if unknown:
            raise LockError(f"no such entries: {', '.join(unknown)}")
        return {name: lock["inputs"][name] for name in sorted(set(names))}
    return dict(entries_for(lock, image))


def field_value(entry: Entry, name: str, field: str) -> str:
    """What `get` prints: url rendered, any string field or derived placeholder, used_by as words."""
    if field == "url":
        return render_url(entry)
    values = placeholders(entry)
    if field in values:
        return values[field]
    value = entry.get(field)
    if isinstance(value, list):
        return " ".join(value)
    raise LockError(f"inputs.{name} has no field {field}")


# ---- commands ---------------------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    read_lock(args.lock, args.image)
    return 0


def cmd_format(args: argparse.Namespace) -> int:
    current = read(args.lock)
    data = loads(current)
    wanted = dump(data)
    if current == wanted:
        return 0
    if args.check or args.lock == "-":
        print(f"{args.lock}: not in its own form; run input-lock format --lock {args.lock}", file=sys.stderr)
        return 1
    Path(args.lock).write_text(wanted, encoding="utf-8")
    print(f"{args.lock}: formatted")
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    lock = read_lock(args.lock)
    entry = selected(lock, None, [args.entry])[args.entry]
    print(field_value(entry, args.entry, args.field))
    return 0


def cmd_env(args: argparse.Namespace) -> int:
    sys.stdout.write(shell(build_env(read_lock(args.lock), args.image)))
    return 0


def cmd_identity(args: argparse.Namespace) -> int:
    lock = read_lock(args.lock)
    print(input_identity([], selected(lock, args.image, args.entry)))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    lock = read_lock(args.lock)
    bad = 0
    for name, entry in selected(lock, None, args.entry).items():
        try:
            findings = fetch.verify(name, entry)
        except (OSError, ValueError) as error:  # report every entry, not just the first that fails
            findings = [fetch.Finding("error", name, str(error))]
        for finding in findings:
            bad += finding.level in ("mismatch", "error")
            print(f"{finding.level:<8} {finding.name}: {finding.message}")
    return 1 if bad else 0


def cmd_refresh(args: argparse.Namespace) -> int:
    lock = read_lock(args.lock)
    if args.entry:
        names = sorted(selected(lock, None, args.entry))
    elif args.base:
        names = moved(loads(file_at(args.base, args.lock)), lock)
    else:
        raise LockError("refresh needs --base <git ref> (the branch a bump goes into) or --entry <name>")
    computed: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    failed = 0
    for name in names:
        try:
            computed[name], sources[name] = fetch.checksums_for(lock["inputs"][name])
        except (OSError, ValueError) as error:
            print(f"error    {name}: {error}", file=sys.stderr)
            failed += 1
    if failed:
        print(f"{args.lock}: nothing written, {failed} of {len(names)} moved entries failed", file=sys.stderr)
        return 1
    new, changes = apply(lock, computed, sources)
    for change in changes:
        print(describe(change))
    unchanged = sorted(set(names) - {change.name for change in changes})
    if unchanged:
        print(f"already right: {', '.join(unchanged)}", file=sys.stderr)
    if changes:
        Path(args.lock).write_text(dump(new), encoding="utf-8")
    return 0


def cmd_sh(_: argparse.Namespace) -> int:
    sys.stdout.write(resources.files("input_lock").joinpath("lock.sh").read_text(encoding="utf-8"))
    return 0


# ---- parsing ----------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(
        prog="input-lock", description="One lock file for every external download a build uses."
    )
    top.add_argument("--version", action="version", version=f"input-lock {__version__}")
    commands = top.add_subparsers(dest="command", required=True, metavar="<command>")

    def command(name: str, summary: str) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=summary, description=summary)
        sub.add_argument(
            "--lock", default=DEFAULT_LOCK, help=f"the lock file ('-' reads stdin; default {DEFAULT_LOCK})"
        )
        return sub

    check = command("check", "check the lock is well formed; exit 1 naming every problem")
    check.add_argument("--image", action="append", default=[], help="a name used_by may list (repeat; default: any)")
    check.set_defaults(run=cmd_check)

    fmt = command("format", "rewrite the lock in its own form (2-space indent, sorted keys)")
    fmt.add_argument("--check", action="store_true", help="only check; exit 1 when it is not")
    fmt.set_defaults(run=cmd_format)

    get = command("get", "print one field of an entry; url comes rendered")
    get.add_argument("entry")
    get.add_argument("field", nargs="?", default="url")
    get.set_defaults(run=cmd_get)

    env = command("env", "print a build.env: LOCK_<ENTRY>_<FIELD>=value for every field a build reads")
    env.add_argument("--image", help="only the entries whose used_by lists this image")
    env.set_defaults(run=cmd_env)

    identity = command("identity", "print the input identity of the lock's entries (all, an image's, or named)")
    identity.add_argument("--image", help="the entries whose used_by lists this image")
    identity.add_argument("--entry", action="append", default=[], help="an entry (repeat)")
    identity.set_defaults(run=cmd_identity)

    verify = command("verify", "download every entry (or the named ones) and compare its checksums")
    verify.add_argument("--entry", action="append", default=[], help="an entry (repeat)")
    verify.set_defaults(run=cmd_verify)

    refresh = command("refresh", "rewrite the checksums of the entries a bump moved")
    refresh.add_argument("--base", help="the branch the bump goes into: entries whose version differs from it move")
    refresh.add_argument("--entry", action="append", default=[], help="refresh these entries instead (repeat)")
    refresh.set_defaults(run=cmd_refresh)

    sh = commands.add_parser("sh", help="print lock.sh, the shell helpers a build sources beside its build.env")
    sh.set_defaults(run=cmd_sh)
    return top


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        code: int = args.run(args)
    except LockError as error:
        for line in str(error).splitlines():
            print(f"{getattr(args, 'lock', DEFAULT_LOCK)}: {line}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"input-lock: {error}", file=sys.stderr)
        return 1
    return code


if __name__ == "__main__":
    sys.exit(main())
