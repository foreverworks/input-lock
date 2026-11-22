"""input-lock: one lock file for every external download a build uses.

The library's public surface, for a build tool that reads the lock instead of re-implementing it:

    from input_lock import parse, render_url, build_env, input_identity

`input_lock.lock` is the format, `input_lock.identity` the input identity, `input_lock.records`
the provenance, SBOM and test-result records, `input_lock.deb` a vendor's apt repository,
`input_lock.signed` what a vendor signed, and `input_lock.fetch` the network and gpgv. Python
standard library only.
"""

__version__ = "0.1.0"

from .identity import IDENTITY, Package, canonical, digest, files_digest, input_identity, lock_digest
from .lock import (
    BOOKKEEPING,
    CHECKSUMS,
    HINTS,
    NOT_BUILD_FIELDS,
    SCHEMA,
    SNAPSHOT_ID,
    TRUST,
    Entry,
    JsonValue,
    Lock,
    LockError,
    Value,
    build_env,
    build_fields,
    checksums,
    dump,
    entries_for,
    entry_env,
    entry_problems,
    env_name,
    loads,
    parse,
    placeholders,
    problems,
    render_url,
    schema_of,
    shell,
    used_by,
)

__all__ = [
    "BOOKKEEPING",
    "CHECKSUMS",
    "HINTS",
    "IDENTITY",
    "NOT_BUILD_FIELDS",
    "SCHEMA",
    "SNAPSHOT_ID",
    "TRUST",
    "Entry",
    "JsonValue",
    "Lock",
    "LockError",
    "Package",
    "Value",
    "__version__",
    "build_env",
    "build_fields",
    "canonical",
    "checksums",
    "digest",
    "dump",
    "entries_for",
    "entry_env",
    "entry_problems",
    "env_name",
    "files_digest",
    "input_identity",
    "loads",
    "lock_digest",
    "parse",
    "placeholders",
    "problems",
    "render_url",
    "schema_of",
    "shell",
    "used_by",
]
