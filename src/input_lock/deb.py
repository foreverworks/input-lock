"""A vendor's Debian repository: where its Packages index is, and what the index says about one .deb.

An `apt` entry pins a .deb from a vendor's repository. Renovate's `deb` datasource finds new
versions from its `registryUrl`, `https://<repository>?suite=<suite>&components=<c1,c2>&binaryArch=<arch>`;
the same URL says where the index is that publishes each version's checksum, and where the signed
`InRelease` is that vouches for the index. Pure functions.
"""

import datetime
import email.utils
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from .lock import Entry, render_url, text

INDEX_CHECKSUMS: Final[Mapping[str, str]] = {"SHA256": "sha256", "SHA512": "sha512", "SHA1": "sha1"}


@dataclass(frozen=True)
class Published:
    """One package at one version, as a Packages index lists it."""

    filename: str
    checksums: Mapping[str, str]


def suite_of(registry_url: str) -> str:
    """The suite a Renovate deb registryUrl names."""
    return urllib.parse.parse_qs(urllib.parse.urlsplit(registry_url).query)["suite"][0]


def dists_url(registry_url: str) -> str:
    """`<repository>/dists/<suite>`: where the suite's InRelease and indices are."""
    parts = urllib.parse.urlsplit(registry_url)
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    return f"{base}/dists/{suite_of(registry_url)}"


def index_urls(registry_url: str) -> list[str]:
    """The Packages index of every component a Renovate deb registryUrl names (binaryArch defaults to amd64)."""
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(registry_url).query)
    arch = query.get("binaryArch", ["amd64"])[0]
    dists = dists_url(registry_url)
    return [f"{dists}/{component}/binary-{arch}/Packages" for component in query["components"][0].split(",")]


def release_fields(release: str) -> dict[str, str]:
    """The fields of a Release file (the signed text of an InRelease): a multi-line field's lines joined by newlines."""
    fields: dict[str, str] = {}
    key = ""
    for line in release.splitlines():
        if not line.strip():
            break  # a Release is one stanza
        if line[0] in " \t" and key:
            fields[key] += "\n" + line.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            fields[key] = value.strip()
    return fields


def release_sums(release: str, suite: str, now: datetime.datetime) -> dict[str, str]:
    """{path under dists/<suite>/: sha256} a signed Release vouches for, after checking it is that suite's and current.

    Raises ValueError for a Release of another suite (an old or foreign signed file served in its
    place), one past its Valid-Until, or one that lists no SHA256."""
    fields = release_fields(release)
    if suite not in (fields.get("Suite"), fields.get("Codename")):
        said = f"suite {fields.get('Suite')!r}, codename {fields.get('Codename')!r}"
        raise ValueError(f"the signed Release is for {said}, not {suite!r}: an old or foreign file in its place")
    if "Valid-Until" in fields:
        try:
            until = email.utils.parsedate_to_datetime(fields["Valid-Until"])
        except (TypeError, ValueError):
            raise ValueError(f"the signed Release's Valid-Until {fields['Valid-Until']!r} is not a date") from None
        if until.tzinfo is None:
            until = until.replace(tzinfo=datetime.UTC)
        if until < now:
            raise ValueError(f"the signed Release expired at {fields['Valid-Until']} (its Valid-Until)")
    sums: dict[str, str] = {}
    for line in fields.get("SHA256", "").splitlines():
        parts = line.split()
        if len(parts) == 3:
            sums[parts[2]] = parts[0].lower()
    if not sums:
        raise ValueError("the signed Release lists no SHA256 checksums")
    return sums


def find(index: str, package: str, version: str) -> Published | None:
    """What a Packages index publishes for `package` at `version`, or None when it does not list it."""
    for stanza in re.split(r"\n\s*\n", index):
        fields = dict(re.findall(r"^([A-Za-z0-9-]+):[ \t]*(.*)$", stanza, re.MULTILINE))
        if fields.get("Package") == package and fields.get("Version") == version:
            sums = {ours: fields[theirs].lower() for theirs, ours in INDEX_CHECKSUMS.items() if theirs in fields}
            return Published(filename=fields.get("Filename", ""), checksums=sums)
    return None


def same_file(entry: Entry, published: Published) -> bool:
    """The entry's url downloads the file the index lists (their last path segments agree)."""
    ours = urllib.parse.unquote(render_url(entry).rsplit("/", 1)[-1])
    theirs = urllib.parse.unquote(published.filename.rsplit("/", 1)[-1])
    return bool(theirs) and ours == theirs


def package(entry: Entry) -> tuple[str, str]:
    """(package, version) an apt entry pins."""
    return text(entry, "apt"), text(entry, "version")
