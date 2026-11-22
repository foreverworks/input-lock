"""The network: hash a download as it streams, read what a vendor's Packages index or signed list
publishes, and check a vendor's signature with gpgv.

Every function here does I/O. Nothing is cached and nothing is trusted that a checksum or a
signature has not checked: a download is hashed with every algorithm asked for, in one pass.
"""

import datetime
import gzip
import hashlib
import http.client
import lzma
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import zlib
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from . import __version__
from .deb import Published, dists_url, find, index_urls, package, release_sums, same_file, suite_of
from .lock import Entry, Lock, checksums, is_checksum, render, render_url, text
from .refresh import wanted
from .signed import clear_signed_problem, dearmor, listed, status_problem, sums_name

TIMEOUT: Final = 120
"""Seconds without a byte before a download is abandoned."""

CHUNK: Final = 1 << 20

ERRORS: Final = (OSError, ValueError, http.client.HTTPException)
"""What a download, an index read or a signature check raises when it fails: the CLI reports these per entry."""

GPGV_TIMEOUT: Final = 60
"""Seconds gpgv may take to check one signature."""


@dataclass(frozen=True)
class Finding:
    """One result of `verify`: a checksum that matched or did not, an error, or a note."""

    level: Literal["ok", "mismatch", "error", "note"]
    name: str
    message: str


def request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": f"input-lock/{__version__}"})


def no_downgrade(url: str, final: str) -> None:
    """Refuse a redirect from https to anything else: the bytes would no longer be the server's."""
    if url.startswith("https://") and not final.startswith("https://"):
        raise ValueError(f"{url} redirected to {final}, which is not https")


def hashes(url: str, algorithms: Sequence[str]) -> dict[str, str]:
    """{algorithm: hex} of the file at `url`, hashed with each algorithm in one streaming pass.

    Raises ValueError when fewer bytes arrive than the server announced: a cut download is never hashed."""
    hashers = {algorithm: hashlib.new(algorithm) for algorithm in algorithms}
    size = 0
    with urllib.request.urlopen(request(url), timeout=TIMEOUT) as response:
        no_downgrade(url, response.geturl())
        announced = response.headers.get("Content-Length")
        while chunk := response.read(CHUNK):
            size += len(chunk)
            for hasher in hashers.values():
                hasher.update(chunk)
    if isinstance(announced, str) and announced.isdigit() and int(announced) != size:
        raise ValueError(f"{url}: {size} bytes arrived of the {announced} announced")
    return {algorithm: hasher.hexdigest() for algorithm, hasher in hashers.items()}


def missing(error: urllib.error.URLError, url: str) -> bool:
    """The server (or the file system) has no such file, as opposed to failing. Object stores answer a
    missing file with 403, so it counts as missing too."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (403, 404, 410)
    return url.startswith("file:")


def body(url: str) -> bytes | None:
    """The whole file at `url`; None when the server has no such file. Raises ValueError for a cut download."""
    try:
        with urllib.request.urlopen(request(url), timeout=TIMEOUT) as response:
            no_downgrade(url, response.geturl())
            announced = response.headers.get("Content-Length")
            data: bytes = response.read()
    except urllib.error.URLError as error:
        if missing(error, url):
            return None
        raise
    if isinstance(announced, str) and announced.isdigit() and int(announced) != len(data):
        raise ValueError(f"{url}: {len(data)} bytes arrived of the {announced} announced")
    return data


def required(url: str) -> bytes:
    """`body`, required: raises ValueError when the server has no such file."""
    data = body(url)
    if data is None:
        raise ValueError(f"{url}: no such file")
    return data


def read_index(url: str, vouched: Mapping[str, str] | None = None) -> str | None:
    """A Packages index as text, from `url` or its .gz or .xz form; None when the repository has none.

    vouched: {index url: sha256} from a signed Release. When given, only a form it lists is read, and
    only when its bytes hash to what it lists; any other is treated as absent."""
    for candidate, unpack in ((url, None), (url + ".gz", gzip.decompress), (url + ".xz", lzma.decompress)):
        if vouched is not None and candidate not in vouched:
            continue
        data = body(candidate)
        if data is None:
            continue
        if vouched is not None and (got := hashlib.sha256(data).hexdigest()) != vouched[candidate]:
            raise ValueError(f"{candidate} hashes to {got}, the signed Release lists {vouched[candidate]}")
        try:
            return (unpack(data) if unpack else data).decode("utf-8", "replace")
        except (OSError, EOFError, lzma.LZMAError, zlib.error) as error:
            raise ValueError(f"{candidate} is not a readable index: {error}") from None
    return None


# ---- signatures --------------------------------------------------------------------------------


def keyring(entry: Entry) -> bytes:
    """The keyring a `signed_by` names, downloaded and checked against every checksum its entry carries,
    unwrapped from ASCII armor when it is armored. Raises ValueError when a checksum does not match."""
    url = render_url(entry)
    data = required(url)
    for algorithm, value in checksums(entry).items():
        if hashlib.new(algorithm, data).hexdigest() != value:
            raise ValueError(
                f"the keyring {url} does not match its {algorithm} in the lock: a keyring moves by hand, "
                "with its new checksum written in the same change"
            )
    return dearmor(data)


def keyring_for(lock: Lock, entry: Entry, cache: MutableMapping[str, bytes] | None = None) -> bytes | None:
    """The keyring of the entry's `signed_by` (`keyring`), or None for an entry without one.

    cache: {keyring entry name: bytes}, filled as keyrings are read, so one run reads each once."""
    signer = text(entry, "signed_by")
    if not signer:
        return None
    if cache is not None and signer in cache:
        return cache[signer]
    if signer not in lock["inputs"]:
        raise ValueError(f"signed_by names {signer}, which is not an entry of the lock")
    data = keyring(lock["inputs"][signer])
    if cache is not None:
        cache[signer] = data
    return data


def gpgv(ring: bytes, signed: bytes, signature: bytes | None = None) -> bytes:
    """The signed text, after gpgv checks its signature against `ring` alone.

    `signed` is a clear-signed message, or, with `signature` (a detached signature), the bytes it
    signs. Raises ValueError when gpgv is not installed, the signature does not pass (`status_problem`),
    or a clear-signed file holds anything beside its one message: never a fall back to HTTPS."""
    tool = shutil.which("gpgv")
    if tool is None:
        raise ValueError(
            "gpgv is not installed, and this entry's signature must be checked: install gpgv "
            "(Debian and Ubuntu: apt-get install gpgv); input-lock never falls back to trusting HTTPS"
        )
    if signature is None and (problem := clear_signed_problem(signed)) is not None:
        raise ValueError(problem)
    with tempfile.TemporaryDirectory(prefix="input-lock-gpgv-") as scratch:
        root = Path(scratch)
        home = root / "home"
        home.mkdir(mode=0o700)
        ring_path, data_path, out_path = root / "keyring", root / "data", root / "signed"
        ring_path.write_bytes(ring)
        data_path.write_bytes(signed)
        if signature is None:
            files = ["--output", str(out_path), str(data_path)]
        else:
            (root / "signature").write_bytes(signature)
            files = [str(root / "signature"), str(data_path)]
        command = [tool, "--homedir", str(home), "--status-fd", "1", "--keyring", str(ring_path), *files]
        try:
            done = subprocess.run(
                command, capture_output=True, stdin=subprocess.DEVNULL, timeout=GPGV_TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired:
            raise ValueError(f"gpgv took longer than {GPGV_TIMEOUT} s") from None
        problem = status_problem(done.returncode, done.stdout.decode("utf-8", "replace"))
        if problem is not None:
            said = " ".join(done.stderr.decode("utf-8", "replace").split())
            raise ValueError(f"the signature does not verify: {problem} ({said})")
        return out_path.read_bytes() if signature is None else signed


def signed_release(registry_url: str, ring: bytes) -> tuple[dict[str, str], str]:
    """{index url: sha256} the repository's signed InRelease (or Release and Release.gpg) vouches for, and
    the URL of the signed file. Raises ValueError when neither is there or the signature does not pass."""
    dists = dists_url(registry_url)
    in_release = body(f"{dists}/InRelease")
    if in_release is not None:
        signed_text, source = gpgv(ring, in_release), f"{dists}/InRelease"
    else:
        release = body(f"{dists}/Release")
        detached = body(f"{dists}/Release.gpg")
        if release is None or detached is None:
            raise ValueError(f"{dists} has no InRelease, nor a Release with its Release.gpg: nothing signed to check")
        signed_text, source = gpgv(ring, release, detached), f"{dists}/Release"
    now = datetime.datetime.now(datetime.UTC)
    sums = release_sums(signed_text.decode("utf-8"), suite_of(registry_url), now)
    return {f"{dists}/{path}": value for path, value in sums.items()}, source


def signed_sums(entry: Entry, ring: bytes) -> tuple[dict[str, str], str]:
    """{algorithm: hex} the vendor's signed list at `sums_url` gives the entry's file, and the list's URL."""
    sums_url = render(entry, "sums_url")
    data = required(sums_url)
    signature = required(render(entry, "sums_sig_url")) if "sums_sig_url" in entry else None
    signed = gpgv(ring, data, signature)
    try:
        return listed(signed.decode("utf-8"), sums_name(render_url(entry), sums_url)), sums_url
    except ValueError as error:
        raise ValueError(f"{sums_url}: {error}") from None


def needed(entry: Entry, ring: bytes | None) -> bytes | None:
    """The keyring, for an entry with `signed_by` (required: none given fails closed); None for one without."""
    if "signed_by" in entry and ring is None:
        raise ValueError(f"signed_by names {text(entry, 'signed_by')}, and no keyring was given to check with")
    return ring if "signed_by" in entry else None


# ---- what a vendor publishes -------------------------------------------------------------------


def lookup(entry: Entry, ring: bytes | None = None) -> tuple[Published, str] | None:
    """What the vendor's index publishes for an apt entry's package and version, and that index's URL;
    None when no index of its registryUrl lists that version. Raises ValueError when the index's file
    is not the one the entry's url downloads.

    An entry with `signed_by` reads only an index its repository's signed Release vouches for, and
    needs `ring`, the keyring (`keyring_for`)."""
    name, version = package(entry)
    ring = needed(entry, ring)
    vouched = signed_release(text(entry, "registryUrl"), ring)[0] if ring is not None else None
    for url in index_urls(text(entry, "registryUrl")):
        index = read_index(url, vouched)
        found = find(index, name, version) if index is not None else None
        if found is None:
            continue
        if not same_file(entry, found):
            raise ValueError(f"{name} {version}: the index's file is {found.filename}, the url is {render_url(entry)}")
        return found, url
    return None


def published(entry: Entry, ring: bytes | None = None) -> tuple[Published, str]:
    """`lookup`, required: raises ValueError when no index lists the entry's version."""
    found = lookup(entry, ring)
    if found is None:
        name, version = package(entry)
        raise ValueError(
            f"{name} {version} is not in {', '.join(index_urls(text(entry, 'registryUrl')))}: the version is not "
            "published there (yet), or the registryUrl is wrong"
        )
    return found


def vendor_sums(entry: Entry, ring: bytes | None = None) -> tuple[dict[str, str], str] | None:
    """{algorithm: hex} the vendor publishes for the entry's file, and where: an apt entry's index, or a
    signed list (`sums_url`). None for an entry with neither, or an apt one its index no longer lists."""
    if "apt" in entry:
        found = lookup(entry, ring)
        return None if found is None else (dict(found[0].checksums), found[1])
    if "sums_url" in entry:
        ring = needed(entry, ring)
        if ring is None:
            raise ValueError("a sums_url is read only with its signature checked, and the entry has no signed_by")
        return signed_sums(entry, ring)
    return None


def checksums_for(entry: Entry, ring: bytes | None = None) -> tuple[dict[str, str], str]:
    """The checksums an entry should carry at its version, and where they came from.

    An apt entry takes what its vendor's index publishes, and an entry with a `sums_url` what the
    vendor's signed list gives its file; anything the vendor lacks is hashed from the download,
    which must then agree with every checksum the vendor gave. Any other entry is downloaded and
    hashed with every algorithm it carries, plus sha256. An entry with `signed_by` needs `ring`,
    its keyring (`keyring_for`)."""
    url = render_url(entry)
    want = wanted(entry)
    if "apt" in entry:
        found, source = published(entry, ring)
        vouched = dict(found.checksums)
    else:
        vouched, source = vendor_sums(entry, ring) or ({}, url)
    for algorithm, value in vouched.items():
        if not is_checksum(algorithm, value):  # a vendor's text: never written into a lock unread
            raise ValueError(f"{source} gives {algorithm} {value!r} for the version, which is not a hex digest")
    got = {algorithm: value for algorithm, value in vouched.items() if algorithm in want}
    rest = [algorithm for algorithm in want if algorithm not in got]
    if rest:
        downloaded = hashes(url, sorted({*rest, *vouched}))
        for algorithm, value in sorted(vouched.items()):
            if downloaded[algorithm] != value:
                raise ValueError(f"{url} hashes to {algorithm} {downloaded[algorithm]}, {source} says {value}")
        got.update({algorithm: downloaded[algorithm] for algorithm in rest})
    return got, source


def verify(name: str, entry: Entry, ring: bytes | None = None) -> list[Finding]:
    """Download the entry and compare every checksum it carries, and every one its vendor publishes
    (an apt entry's index, a signed list). An entry with `signed_by` needs `ring` (`keyring_for`)."""
    url = render_url(entry)
    have = checksums(entry)
    vendor = vendor_sums(entry, ring)
    got = hashes(url, sorted({*have, *(vendor[0] if vendor else {})}))
    findings = [
        Finding("ok" if got[algorithm] == value else "mismatch", name, f"{algorithm} of {url}")
        for algorithm, value in have.items()
    ]
    if vendor is None and "apt" in entry:  # a vendor that keeps only its newest versions in the index
        findings.append(
            Finding("note", name, "its index no longer lists this version; checked against the download alone")
        )
    elif vendor is not None:
        sums, source = vendor
        for algorithm, says in sorted(sums.items()):
            if says != got[algorithm]:
                message = f"{source} says {algorithm} {says}, the download hashes to {got[algorithm]}"
                findings.append(Finding("mismatch", name, message))
        if "signed_by" in entry:
            findings.append(Finding("ok", name, f"signature of {source}"))
    return findings
