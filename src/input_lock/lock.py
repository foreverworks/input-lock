"""The lock file: its format, its checks, its canonical text and what a build reads from it.

A lock is a JSON object:

    {
      "schema": 1,                          optional; a lock without it is schema 1; 2 when it uses TRUST
      "debian_snapshot": "20261121T005332Z" optional; a snapshot.debian.org ID for Debian builds
      "inputs": { "<name>": <entry>, ... }  one entry per downloaded file
    }

An entry pins one download: its `version`, a `url` template built from that version, and at least
one checksum. Renovate's lookup hints sit beside the version, so Renovate can move it. Every other
lowercase field is the consumer's own, a string a build may read. docs/format.md is the reference.

Everything here is a pure function over plain data. Reading files, the network and git live in
`input_lock.fetch` and `input_lock.cli`.
"""

import json
import re
import shlex
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Final, NotRequired, TypedDict, cast

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
"""What `json.loads` returns, for narrowing untrusted input."""

type Value = str | list[str] | None
"""A field of a well-formed entry: a string, `used_by`'s list, or `module`'s null."""

type Entry = Mapping[str, Value]
"""One input of a well-formed lock, field by field, as the file holds it."""


class Lock(TypedDict):
    """A lock that passed `problems`, as the file holds it."""

    schema: NotRequired[int]
    debian_snapshot: NotRequired[str]
    inputs: dict[str, dict[str, Value]]


class LockError(ValueError):
    """The lock is not one this library can read: its message names every problem, one per line."""


SCHEMA: Final = 2
"""The highest schema this library reads. A lock is schema 1 unless it uses a TRUST field, which needs
schema 2: input-lock 0.1.0 reads schema 1 only, so it refuses such a lock instead of reading its
signature fields as plain fields and checking nothing. A lock with a higher schema needs a newer release."""

CHECKSUMS: Final[Mapping[str, int]] = {"sha256": 64, "sha512": 128, "sha1": 40}
"""Every checksum an entry may carry, and its length in hex digits."""

HINTS: Final = ("datasource", "depName", "packageName", "extractVersion", "versioning", "registryUrl")
"""Renovate's lookup: how to find the next version. A build never reads them."""

BOOKKEEPING: Final = ("used_by", "description")
"""Fields about the entry, for people and for the consumer's own wiring. A build never reads them."""

TRUST: Final = ("signed_by", "sums_url", "sums_sig_url")
"""How the checksum is vouched for: the keyring entry the vendor signs with, and its signed checksum list.
A build never reads them: the checksum they vouch for is what counts."""

NOT_BUILD_FIELDS: Final = frozenset((*HINTS, *BOOKKEEPING, *TRUST))
"""The deny-list: the fields that stay out of `build_fields`, so out of identities and build.env."""

DERIVED: Final = ("version_noepoch", "version_underscore", "version_urlquoted", "major")
"""Placeholders a url template may use that the version derives; no entry may define them."""

TOP_LEVEL: Final = ("schema", "debian_snapshot", "inputs")
STRING_FIELDS: Final = frozenset(("version", "url", "apt", "description", *HINTS, *CHECKSUMS, *TRUST))
TEMPLATES: Final = ("url", "sums_url", "sums_sig_url")
"""The fields that are url templates: rendered from the entry's placeholders, https only."""
NAME: Final = re.compile(r"[a-z0-9][a-z0-9-]*")
FIELD: Final = re.compile(r"[a-z][a-z0-9_]*")
PLACEHOLDER: Final = re.compile(r"\{([a-z][a-z0-9_]*)\}")
SNAPSHOT_ID: Final = re.compile(r"\d{8}T\d{6}Z")
"""A snapshot.debian.org ID, YYYYMMDDTHHMMSSZ: what `debian_snapshot` holds. Match it with `fullmatch`."""
URL_SCHEMES: tuple[str, ...] = ("https://",)
"""The schemes a url may use. Tests widen it to file:// for offline downloads."""


# ---- reading and writing the text -------------------------------------------------------------


def loads(text: str) -> JsonValue:
    """The lock's JSON, unchecked. Empty text is an empty lock."""
    if not text.strip():
        return {"inputs": {}}
    try:
        value: JsonValue = json.loads(text, object_pairs_hook=unique_keys)
    except json.JSONDecodeError as error:
        raise LockError(f"not JSON: {error}") from None
    return value


def unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    """A JSON object, refusing a key it repeats: JSON keeps the last silently, which would drop an entry."""
    seen: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in seen:
            raise LockError(f"{key!r} appears twice in one object")
        seen[key] = value
    return seen


def parse(text: str, images: Iterable[str] | None = None) -> Lock:
    """The lock in `text`, checked. Raises LockError naming every problem. `images`: see `problems`."""
    data = loads(text)
    found = problems(data, images)
    if found:
        raise LockError("\n".join(found))
    return cast(Lock, data)


def dump(lock: object) -> str:
    """The lock as its file holds it: 2-space indent, sorted keys, UTF-8, a trailing newline, and the
    schema it needs (`with_schema`).

    Tools and people write the same bytes, so a diff shows only what moved."""
    return json.dumps(with_schema(lock), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def schema_needed(data: JsonValue) -> int:
    """The lowest schema that can hold `data`: 2 when an entry uses a TRUST field, else 1."""
    inputs = data.get("inputs") if isinstance(data, dict) else None
    if isinstance(inputs, dict) and any(
        isinstance(entry, dict) and any(field in entry for field in TRUST) for entry in inputs.values()
    ):
        return 2
    return 1


def with_schema(lock: object) -> object:
    """The lock with `"schema": 2` when it needs it, and a 2 it no longer needs lowered to 1; else as it is.

    A lock without a schema field gains none unless it needs 2, so a schema-1 lock keeps its bytes."""
    if not isinstance(lock, dict) or not isinstance(lock.get("inputs"), dict):
        return lock
    needed = schema_needed(cast(JsonValue, lock))
    if needed > 1:
        return {**lock, "schema": needed}
    if lock.get("schema") == 2:
        return {**lock, "schema": 1}
    return lock


def schema_of(data: Mapping[str, JsonValue]) -> int:
    """The lock's schema: 1 when it has none. Raises LockError for one this release cannot read."""
    schema = data.get("schema", 1)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise LockError(f"schema must be a positive integer, not {schema!r}")
    if schema > SCHEMA:
        raise LockError(
            f"this lock is schema {schema}, and this release of input-lock reads schema {SCHEMA} only: "
            "upgrade input-lock to read it"
        )
    return schema


# ---- what an entry says ------------------------------------------------------------------------


def text(entry: Entry, field: str) -> str:
    """A string field of an entry, or '' when it has none."""
    value = entry.get(field)
    return value if isinstance(value, str) else ""


def placeholders(entry: Entry) -> dict[str, str]:
    """What a url template may name: the entry's own string fields and the version's derived forms.

    {version_noepoch} drops a Debian epoch ("5:29.1.0-1" is "29.1.0-1"), {version_underscore}
    writes "+" as "_", {version_urlquoted} percent-encodes the whole version, and {major} is the
    leading digits of the version without its epoch."""
    version = text(entry, "version")
    noepoch = re.sub(r"^\d+:", "", version)
    major = re.match(r"\d*", noepoch)
    own = {key: value for key, value in entry.items() if isinstance(value, str)}
    return {
        **own,
        "version_noepoch": noepoch,
        "version_underscore": version.replace("+", "_"),
        "version_urlquoted": urllib.parse.quote(version, safe=""),
        "major": major.group(0) if major else "",
    }


def render_url(entry: Entry) -> str:
    """The entry's download URL: its `url` template with every {placeholder} filled.

    Raises ValueError for a placeholder the entry does not have."""
    return render(entry, "url")


def render(entry: Entry, field: str) -> str:
    """A url template field of the entry (`url`, `sums_url`, `sums_sig_url`) with every {placeholder} filled.

    Raises ValueError for a placeholder the entry does not have."""
    values = placeholders(entry)

    def one(match: re.Match[str]) -> str:
        if match.group(1) not in values:
            raise ValueError(f"{field} names {{{match.group(1)}}}, which the entry does not have")
        return values[match.group(1)]

    return PLACEHOLDER.sub(one, text(entry, field))


def checksums(entry: Entry) -> dict[str, str]:
    """{algorithm: lowercase hex} of every checksum the entry carries."""
    return {algorithm: value for algorithm in CHECKSUMS if isinstance(value := entry.get(algorithm), str)}


def is_checksum(algorithm: str, value: str) -> bool:
    """`value` is what `algorithm` prints: lowercase hex of the algorithm's length."""
    return algorithm in CHECKSUMS and re.fullmatch(f"[0-9a-f]{{{CHECKSUMS[algorithm]}}}", value) is not None


def used_by(entry: Entry) -> list[str] | None:
    """The images (or any other units) that install this input, or None when the entry does not say."""
    value = entry.get("used_by")
    return list(value) if isinstance(value, list) else None


def build_fields(entry: Entry) -> dict[str, Value]:
    """The fields a build reads: every field but Renovate's hints and the bookkeeping, `url` rendered.

    This is a deny-list on purpose. A field this library does not know (a consumer's own `build`
    or `sdk`) is read by someone's build, so it counts; only fields known to be irrelevant to the
    bytes a build makes stay out. The input identity and build.env are both this projection."""
    fields = {key: value for key, value in entry.items() if key not in NOT_BUILD_FIELDS}
    if "url" in fields:
        fields["url"] = render_url(entry)
    return fields


def entries_for(lock: Lock, image: str | None = None) -> dict[str, dict[str, Value]]:
    """The entries `image` lists in its `used_by`, sorted by name; every entry when image is None."""
    return {
        name: entry
        for name, entry in sorted(lock["inputs"].items())
        if image is None or image in (used_by(entry) or [])
    }


# ---- build.env --------------------------------------------------------------------------------


def env_name(name: str) -> str:
    """LOCK_<NAME>: an entry's prefix in build.env, dashes as underscores."""
    return "LOCK_" + re.sub(r"[^A-Z0-9]", "_", name.upper())


def entry_env(name: str, entry: Entry) -> list[tuple[str, str]]:
    """[(LOCK_<NAME>_<FIELD>, value)] for every string field the build reads, url rendered."""
    return [
        (f"{env_name(name)}_{field.upper()}", value)
        for field, value in sorted(build_fields(entry).items())
        if isinstance(value, str)
    ]


def build_env(lock: Lock, image: str | None = None, *, snapshot: bool = True) -> list[tuple[str, str]]:
    """[(KEY, value)] for a build.env: the snapshot (when the lock has one), LOCK_ENTRIES, every entry's fields.

    snapshot=False leaves LOCK_DEBIAN_SNAPSHOT out, for an environment that must not see the build
    snapshot (a hook's, say), even when the lock has one."""
    own = entries_for(lock, image)
    pairs: list[tuple[str, str]] = []
    if snapshot and "debian_snapshot" in lock:
        pairs.append(("LOCK_DEBIAN_SNAPSHOT", lock["debian_snapshot"]))
    pairs.append(("LOCK_ENTRIES", " ".join(own)))
    for name, entry in own.items():
        pairs.extend(entry_env(name, entry))
    return pairs


def shell(pairs: Iterable[tuple[str, str]]) -> str:
    """KEY=value lines a POSIX shell can source, each value quoted as one word."""
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in pairs)


# ---- the checks -------------------------------------------------------------------------------


def problems(data: JsonValue, images: Iterable[str] | None = None) -> list[str]:
    """Every way `data` is not a well-formed lock, one sentence each; [] when it is one.

    images: when given, every `used_by` may name only these (the consumer's list of images)."""
    if not isinstance(data, dict):
        return ["a lock is a JSON object"]
    try:
        schema = schema_of(data)
    except LockError as error:
        return [str(error)]
    found: list[str] = []
    if schema_needed(data) > schema:
        found.append(
            f'the lock uses {", ".join(TRUST)}, which need "schema": 2, so that input-lock 0.1.0 refuses it '
            "instead of checking no signature: run input-lock format"
        )
    unknown = sorted(set(data) - set(TOP_LEVEL))
    if unknown:
        found.append(f"unknown top-level fields {unknown}: a lock holds {list(TOP_LEVEL)}")
    snapshot = data.get("debian_snapshot")
    if "debian_snapshot" in data and not (isinstance(snapshot, str) and SNAPSHOT_ID.fullmatch(snapshot)):
        found.append(f"debian_snapshot {snapshot!r} is not a snapshot.debian.org ID (YYYYMMDDTHHMMSSZ)")
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        return [*found, "inputs must be an object, one entry per downloaded file"]
    allowed = None if images is None else frozenset(images)
    good: dict[str, Entry] = {}
    for name, entry in inputs.items():
        mistakes = entry_problems(name, entry, allowed)
        found.extend(mistakes)
        if not mistakes:
            good[name] = cast(Entry, entry)
    found.extend(across_entries(good))
    found.extend(keyring_problems(good, frozenset(inputs)))
    return found


def field_problems(where: str, entry: Mapping[str, JsonValue]) -> list[str]:
    """Each field has the type its name says; an unknown field is a lowercase string field."""
    found: list[str] = []
    for key, value in entry.items():
        if key in STRING_FIELDS:
            if not isinstance(value, str):
                found.append(f"{where}.{key} must be a string")
        elif key == "module":
            if value is not None and not (isinstance(value, str) and value):
                found.append(f"{where}.module must be a name or null")
        elif key == "used_by":
            if not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
                found.append(f"{where}.used_by must be a list of names")
        elif key in DERIVED:
            found.append(f"{where}.{key} is a placeholder the version derives, so it cannot be a field")
        elif not FIELD.fullmatch(key):
            found.append(f"{where}: unknown field {key!r} (a field of your own is lowercase letters, digits and _)")
        elif not isinstance(value, str):
            found.append(f"{where}.{key} must be a string (a field of your own is a string a build reads)")
    return found


def entry_problems(name: str, raw: JsonValue, images: frozenset[str] | None = None) -> list[str]:
    """Every way one entry is malformed; [] when it is well formed."""
    where = f"inputs.{name}"
    if not NAME.fullmatch(name):
        return [f"{where}: an entry's name is lowercase letters, digits and dashes"]
    if not isinstance(raw, dict):
        return [f"{where}: not an object"]
    found = field_problems(where, raw)
    if found:
        return found
    entry = cast(Entry, raw)
    if not text(entry, "version").strip():
        found.append(f"{where}: no version")
    found.extend(url_problems(where, entry))
    sums = checksums(entry)
    if not sums:
        found.append(f"{where}: no checksum ({', '.join(CHECKSUMS)})")
    for algorithm, value in sums.items():
        if not is_checksum(algorithm, value):
            found.append(f"{where}.{algorithm} is not {CHECKSUMS[algorithm]} lowercase hex digits")
    found.extend(used_by_problems(where, entry, images))
    found.extend(renovate_problems(where, entry))
    found.extend(trust_problems(where, name, entry))
    return found


def url_problems(where: str, entry: Entry) -> list[str]:
    if "url" not in entry:
        return [f"{where}: no url"]
    try:
        url = render_url(entry)
    except ValueError as error:
        return [f"{where}: {error}"]
    found = []
    if not url.startswith(URL_SCHEMES):
        found.append(f"{where}: url is not https")
    template = text(entry, "url")
    if "datasource" in entry and "{version" not in template and "{major}" not in template:
        found.append(f"{where}: Renovate moves its version, so its url must be built from it ({{version}}, ...)")
    return found


def trust_problems(where: str, name: str, entry: Entry) -> list[str]:
    """signed_by, sums_url and sums_sig_url fit together: what the entry alone can say."""
    found: list[str] = []
    signer = text(entry, "signed_by")
    if "signed_by" in entry and not NAME.fullmatch(signer):
        found.append(f"{where}.signed_by names an entry: lowercase letters, digits and dashes")
    if signer == name:
        found.append(f"{where}: an entry cannot be signed_by itself")
    if "sums_url" in entry and "signed_by" not in entry:
        found.append(f"{where}: sums_url is a signed checksum list, so it needs signed_by")
    if "sums_sig_url" in entry and "sums_url" not in entry:
        found.append(f"{where}: sums_sig_url is the signature of a sums_url, which the entry does not have")
    if "signed_by" in entry and "apt" in entry and "sums_url" in entry:
        found.append(f"{where}: an apt entry's signed list is its repository's InRelease, so it takes no sums_url")
    if "signed_by" in entry and "apt" not in entry and "sums_url" not in entry:
        found.append(f"{where}: signed_by needs a sums_url, the vendor's signed checksum list (apt entries need none)")
    for field in ("sums_url", "sums_sig_url"):
        if field not in entry:
            continue
        try:
            url = render(entry, field)
        except ValueError as error:
            found.append(f"{where}: {error}")
            continue
        if not url.startswith(URL_SCHEMES):
            found.append(f"{where}: {field} is not https")
    return found


def keyring_problems(entries: Mapping[str, Entry], names: frozenset[str]) -> list[str]:
    """Every signed_by names an entry of the lock, a keyring that is not signed itself and is no .deb."""
    found: list[str] = []
    for name, entry in sorted(entries.items()):
        signer = text(entry, "signed_by")
        if not signer:
            continue
        if signer not in names:
            found.append(f"inputs.{name}.signed_by names {signer}, which is not an entry of this lock")
        elif signer in entries and ("signed_by" in entries[signer] or "apt" in entries[signer]):
            found.append(
                f"inputs.{name}.signed_by names {signer}, which is a trust root only when it is pinned by its "
                "checksum alone (no signed_by, no apt)"
            )
        elif signer in entries and not {"sha256", "sha512"} & set(checksums(entries[signer])):
            found.append(f"inputs.{name}.signed_by names {signer}, a keyring, which must be pinned by sha256 or sha512")
    return found


def used_by_problems(where: str, entry: Entry, images: frozenset[str] | None) -> list[str]:
    names = used_by(entry)
    if names is None:
        return []
    if not names or names != sorted(set(names)):
        return [f"{where}.used_by must be a sorted list of distinct names, not empty"]
    if images is not None and set(names) - images:
        return [f"{where}.used_by names {sorted(set(names) - images)}, which are not among {sorted(images)}"]
    return []


def renovate_problems(where: str, entry: Entry) -> list[str]:
    found = []
    if ("datasource" in entry) != ("depName" in entry):
        found.append(f"{where}: datasource and depName come together")
    if entry.get("datasource") == "deb":
        if not text(entry, "registryUrl").startswith(URL_SCHEMES):
            found.append(f"{where}: registryUrl is not https (its index is where the checksum comes from)")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(text(entry, "registryUrl")).query)
        if not {"suite", "components"} <= set(query):
            found.append(
                f"{where}: a deb entry's registryUrl names its suite and components (?suite=...&components=...)"
            )
    if "apt" in entry:
        if entry.get("datasource") != "deb" or "registryUrl" not in entry or "sha256" not in entry:
            found.append(f"{where}: an apt entry has datasource deb, a registryUrl and a sha256")
        if not text(entry, "url").endswith(".deb"):
            found.append(f"{where}: an apt entry's url is the .deb")
    return found


def across_entries(entries: Mapping[str, Entry]) -> list[str]:
    """Rules between entries: one entry per apt package, and no two fields with one build.env name."""
    found: list[str] = []
    apt: dict[str, str] = {}
    keys: dict[str, str] = {}
    for name, entry in sorted(entries.items()):
        package = text(entry, "apt")
        if package and package in apt:
            found.append(f"inputs.{name}: apt package {package} is also inputs.{apt[package]}")
        apt.setdefault(package, name)
        try:
            pairs = entry_env(name, entry)
        except ValueError:
            continue  # its url already has a problem of its own
        for key, _ in pairs:
            if key in keys and keys[key] != name:
                found.append(f"inputs.{name}: {key} in build.env would also be inputs.{keys[key]}'s")
            keys.setdefault(key, name)
    return found
