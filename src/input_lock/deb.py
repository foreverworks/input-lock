"""A vendor's Debian repository: where its Packages index is, and what the index says about one .deb.

An `apt` entry pins a .deb from a vendor's repository. Renovate's `deb` datasource finds new
versions from its `registryUrl`, `https://<repository>?suite=<suite>&components=<c1,c2>&binaryArch=<arch>`;
the same URL says where the index is that publishes each version's checksum. Pure functions.
"""

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


def index_urls(registry_url: str) -> list[str]:
    """The Packages index of every component a Renovate deb registryUrl names (binaryArch defaults to amd64)."""
    parts = urllib.parse.urlsplit(registry_url)
    query = urllib.parse.parse_qs(parts.query)
    base = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
    suite = query["suite"][0]
    arch = query.get("binaryArch", ["amd64"])[0]
    return [
        f"{base}/dists/{suite}/{component}/binary-{arch}/Packages" for component in query["components"][0].split(",")
    ]


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
