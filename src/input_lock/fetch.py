"""The network: hash a download as it streams, and read what a vendor's Packages index publishes.

Every function here does I/O. Nothing is cached and nothing is trusted that a checksum has not
checked: a download is hashed with every algorithm asked for, in one pass.
"""

import gzip
import hashlib
import lzma
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from . import __version__
from .deb import Published, find, index_urls, package, same_file
from .lock import Entry, checksums, render_url, text
from .refresh import wanted

TIMEOUT: Final = 120
"""Seconds without a byte before a download is abandoned."""

CHUNK: Final = 1 << 20


@dataclass(frozen=True)
class Finding:
    """One result of `verify`: a checksum that matched or did not, an error, or a note."""

    level: Literal["ok", "mismatch", "error", "note"]
    name: str
    message: str


def request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": f"input-lock/{__version__}"})


def hashes(url: str, algorithms: Sequence[str]) -> dict[str, str]:
    """{algorithm: hex} of the file at `url`, hashed with each algorithm in one streaming pass."""
    hashers = {algorithm: hashlib.new(algorithm) for algorithm in algorithms}
    with urllib.request.urlopen(request(url), timeout=TIMEOUT) as response:
        while chunk := response.read(CHUNK):
            for hasher in hashers.values():
                hasher.update(chunk)
    return {algorithm: hasher.hexdigest() for algorithm, hasher in hashers.items()}


def missing(error: urllib.error.URLError, url: str) -> bool:
    """The server (or the file system) has no such file, as opposed to failing."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code in (403, 404, 410)
    return url.startswith("file:")


def read_index(url: str) -> str | None:
    """A Packages index as text, from `url` or its .gz or .xz form; None when the repository has none."""
    for candidate, unpack in ((url, None), (url + ".gz", gzip.decompress), (url + ".xz", lzma.decompress)):
        try:
            with urllib.request.urlopen(request(candidate), timeout=TIMEOUT) as response:
                body: bytes = response.read()
        except urllib.error.URLError as error:
            if missing(error, candidate):
                continue
            raise
        return (unpack(body) if unpack else body).decode("utf-8", "replace")
    return None


def lookup(entry: Entry) -> tuple[Published, str] | None:
    """What the vendor's index publishes for an apt entry's package and version, and that index's URL;
    None when no index of its registryUrl lists that version. Raises ValueError when the index's file
    is not the one the entry's url downloads."""
    name, version = package(entry)
    for url in index_urls(text(entry, "registryUrl")):
        index = read_index(url)
        found = find(index, name, version) if index is not None else None
        if found is None:
            continue
        if not same_file(entry, found):
            raise ValueError(f"{name} {version}: the index's file is {found.filename}, the url is {render_url(entry)}")
        return found, url
    return None


def published(entry: Entry) -> tuple[Published, str]:
    """`lookup`, required: raises ValueError when no index lists the entry's version."""
    found = lookup(entry)
    if found is None:
        name, version = package(entry)
        raise ValueError(
            f"{name} {version} is not in {', '.join(index_urls(text(entry, 'registryUrl')))}: the version is not "
            "published there (yet), or the registryUrl is wrong"
        )
    return found


def checksums_for(entry: Entry) -> tuple[dict[str, str], str]:
    """The checksums an entry should carry at its version, and where they came from.

    An apt entry takes what its vendor's index publishes; anything the index lacks is hashed from
    the download, which must then agree with the index's sha256. Any other entry is downloaded and
    hashed with every algorithm it carries, plus sha256."""
    url = render_url(entry)
    want = wanted(entry)
    got: dict[str, str] = {}
    source = url
    if "apt" in entry:
        found, source = published(entry)
        got = {algorithm: value for algorithm, value in found.checksums.items() if algorithm in want}
    rest = [algorithm for algorithm in want if algorithm not in got]
    if rest:
        cross = ["sha256"] if "sha256" in got else []
        downloaded = hashes(url, [*rest, *cross])
        if cross and downloaded["sha256"] != got["sha256"]:
            raise ValueError(f"{url} hashes to {downloaded['sha256']}, the index says {got['sha256']}")
        got.update({algorithm: downloaded[algorithm] for algorithm in rest})
    return got, source


def verify(name: str, entry: Entry) -> list[Finding]:
    """Download the entry and compare every checksum it carries; an apt entry's index is compared too."""
    url = render_url(entry)
    have = checksums(entry)
    got = hashes(url, list(have))
    findings = [
        Finding("ok" if got[algorithm] == value else "mismatch", name, f"{algorithm} of {url}")
        for algorithm, value in have.items()
    ]
    if "apt" in entry:
        listed = lookup(entry)
        if listed is None:  # a vendor that keeps only its newest versions in the index
            findings.append(
                Finding("note", name, "its index no longer lists this version; checked against the download alone")
            )
        elif (says := listed[0].checksums.get("sha256")) != got.get("sha256"):
            message = f"{listed[1]} says sha256 {says}, the download hashes to {got.get('sha256')}"
            findings.append(Finding("mismatch", name, message))
    return findings
