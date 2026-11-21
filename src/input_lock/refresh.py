"""Which entries a version bump moved, and the lock with their new checksums. Pure functions.

The network half (downloading, reading a vendor's index) is `input_lock.fetch`; git is the CLI's.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from .lock import CHECKSUMS, JsonValue, Lock, Value, checksums, render_url


@dataclass(frozen=True)
class Change:
    """One checksum rewritten: of which entry, from what to what, and where the new value came from."""

    name: str
    algorithm: str
    old: str | None
    new: str
    url: str
    source: str


def base_versions(base: JsonValue) -> dict[str, str]:
    """{name: version} of a lock as it was before the bump. It may be old or malformed: read what is there."""
    inputs = base.get("inputs") if isinstance(base, dict) else None
    if not isinstance(inputs, dict):
        return {}
    return {
        name: version
        for name, entry in inputs.items()
        if isinstance(entry, dict) and isinstance(version := entry.get("version"), str)
    }


def moved(base: JsonValue, lock: Lock) -> list[str]:
    """The entries whose version differs from `base` (the lock at the merge base), new entries included."""
    before = base_versions(base)
    return sorted(name for name, entry in lock["inputs"].items() if before.get(name) != entry.get("version"))


def wanted(entry: Mapping[str, Value]) -> list[str]:
    """The checksums a moved entry should carry: the ones it has, and always a sha256."""
    have = checksums(entry)
    return [algorithm for algorithm in CHECKSUMS if algorithm in have or algorithm == "sha256"]


def apply(
    lock: Lock, computed: Mapping[str, Mapping[str, str]], sources: Mapping[str, str]
) -> tuple[Lock, list[Change]]:
    """The lock with `computed` ({name: {algorithm: hex}}) written in, and what changed."""
    inputs = {name: dict(entry) for name, entry in lock["inputs"].items()}
    changes: list[Change] = []
    for name, sums in sorted(computed.items()):
        entry = inputs[name]
        for algorithm, value in sorted(sums.items()):
            old = entry.get(algorithm)
            if old == value:
                continue
            entry[algorithm] = value
            changes.append(
                Change(
                    name=name,
                    algorithm=algorithm,
                    old=old if isinstance(old, str) else None,
                    new=value,
                    url=render_url(entry),
                    source=sources.get(name, ""),
                )
            )
    new: Lock = {"inputs": inputs}
    if "schema" in lock:
        new["schema"] = lock["schema"]
    if "debian_snapshot" in lock:
        new["debian_snapshot"] = lock["debian_snapshot"]
    return new, changes


def describe(change: Change) -> str:
    """One line for a log or a commit message."""
    return f"{change.name}: {change.algorithm} {change.new} (from {change.source or change.url})"
