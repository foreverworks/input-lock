"""The input identity: one digest of everything a build is made from, so it rebuilds only when that moves.

    input_identity(packages, entries, recipe_digest, parent_identity) = sha256 of the canonical JSON of
    {
      "identity": 2,
      "packages": the sorted set of (name, version, architecture) the build installs,
      "lock":     {name: build_fields(entry)} of the lock entries it installs itself,
      "recipe":   the digest of its recipe (the files and settings that build it), or null,
      "parent":   the input identity of what it builds on, or null
    }

`build_fields` is the entry minus Renovate's lookup hints and the bookkeeping (`used_by`,
`description`), with `url` rendered: a change to how Renovate finds the next version, or another
image starting to use an entry, never rebuilds anything, while any field a build may read does,
including fields this library has never heard of. Tag 2 marks that projection; tag 1 was a digest
of whole entries, and the two never compare equal.

Canonical JSON is sorted keys, no whitespace, UTF-8: key order and file layout never matter.
"""

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Final

from .lock import Entry, Lock, build_fields, dump

IDENTITY: Final = 2
HEX64: Final = re.compile(r"[0-9a-f]{64}")

type Package = tuple[str, str, str]
"""(name, version, architecture) of one installed system package."""


def canonical(value: object) -> bytes:
    """The bytes a digest is taken over: sorted keys, no whitespace, UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: object) -> str:
    """sha256 hex of a value's canonical JSON."""
    return hashlib.sha256(canonical(value)).hexdigest()


def files_digest(files: Mapping[str, bytes]) -> str:
    """The digest of a set of files, {path: content}: order-free, a rename or an edit moves it."""
    return digest(sorted([path, hashlib.sha256(content).hexdigest()] for path, content in files.items()))


def lock_digest(lock: Lock) -> str:
    """sha256 hex of the lock file's bytes in its own form: what `sha256sum lock.json` prints for a formatted lock."""
    return hashlib.sha256(dump(lock).encode()).hexdigest()


def require_hex(value: str | None, what: str) -> None:
    if value is not None and not HEX64.fullmatch(value):
        raise ValueError(f"{what} is not a sha256 hex digest: {value!r}")


def input_identity(
    packages: Iterable[Package],
    entries: Mapping[str, Entry],
    recipe_digest: str | None = None,
    parent_identity: str | None = None,
) -> str:
    """sha256 hex of what a build is made from. Every argument is order-free and repeats count once.

    packages: the system packages it installs; entries: the lock entries it installs itself (not its
    parent's: those are in the parent's identity); recipe_digest and parent_identity: sha256 hex, or
    None (a build with no recipe of its own, or no parent)."""
    require_hex(recipe_digest, "recipe digest")
    require_hex(parent_identity, "parent identity")
    package_set = sorted({(str(name), str(version), str(arch)) for name, version, arch in packages})
    return digest(
        {
            "identity": IDENTITY,
            "packages": [list(package) for package in package_set],
            "lock": {name: build_fields(entry) for name, entry in entries.items()},
            "recipe": recipe_digest,
            "parent": parent_identity,
        }
    )
