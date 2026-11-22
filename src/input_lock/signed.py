"""What a vendor signed, read without trusting anything around it. Pure functions.

A signature is checked by `gpgv` (`input_lock.fetch` runs it); this module reads what gpgv
reports, refuses a clear-signed file that carries more than its one signed message, unwraps an
ASCII-armored keyring, and finds an entry's file in a vendor's checksum list.
"""

import base64
import binascii
import re
import urllib.parse
from collections.abc import Mapping
from typing import Final

from .lock import CHECKSUMS

GOOD: Final = "VALIDSIG"
"""gpgv's status keyword for a signature that verified with a key of the keyring."""

REFUSED: Final = ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG")
"""gpgv's status keywords for a signature that is bad, uncheckable, expired, or by an expired or revoked key.
Any one of them fails the check, even beside a good signature."""

BY_LENGTH: Final[Mapping[int, str]] = {length: algorithm for algorithm, length in CHECKSUMS.items()}
"""The algorithm a checksum list's hex digest is, by its length."""

ARMOR: Final = re.compile(
    rb"-----BEGIN PGP PUBLIC KEY BLOCK-----[^\n]*\n(.*?)-----END PGP PUBLIC KEY BLOCK-----", re.DOTALL
)
CLEAR_BEGIN: Final = b"-----BEGIN PGP SIGNED MESSAGE-----"
SIGNATURE_BEGIN: Final = b"-----BEGIN PGP SIGNATURE-----"
SIGNATURE_END: Final = b"-----END PGP SIGNATURE-----"
SUMS_LINE: Final = re.compile(r"([0-9A-Fa-f]+) [ *](.+)")


def status_problem(returncode: int, status: str) -> str | None:
    """Why gpgv's verdict is not a pass, or None when it is one.

    `status` is what `gpgv --status-fd` wrote. A pass is exit 0, at least one VALIDSIG, and none of
    REFUSED: gpgv's exit code alone does not tell an expired key from a good one."""
    words = [line.split()[1] for line in status.splitlines() if line.startswith("[GNUPG:] ") and len(line.split()) > 1]
    refused = sorted({word for word in words if word in REFUSED})
    if refused:
        return f"gpgv reports {', '.join(refused)}"
    if returncode != 0:
        return f"gpgv exited {returncode}"
    if GOOD not in words:
        return "gpgv reports no valid signature"
    return None


def clear_signed_problem(data: bytes) -> str | None:
    """Why `data` is not exactly one clear-signed message, or None when it is.

    Only whitespace may come before the message or after its signature, and it holds one of each:
    text outside the signed part could otherwise be read as if it were signed."""
    body = data.strip()
    if not body.startswith(CLEAR_BEGIN):
        return "not a clear-signed message (it does not begin with -----BEGIN PGP SIGNED MESSAGE-----)"
    if not body.endswith(SIGNATURE_END):
        return "something follows the signature of the clear-signed message"
    if body.count(CLEAR_BEGIN) != 1 or body.count(SIGNATURE_BEGIN) != 1 or body.count(SIGNATURE_END) != 1:
        return "more than one signed message in one file"
    return None


def dearmor(data: bytes) -> bytes:
    """A keyring as gpgv reads it: an ASCII-armored one unwrapped, a binary one as it is.

    Raises ValueError for armor that holds no public key block or is not base64."""
    if not data.lstrip().startswith(b"-----BEGIN PGP"):
        return data
    blocks = ARMOR.findall(data)
    if not blocks:
        raise ValueError("the keyring is ASCII-armored but holds no PUBLIC KEY BLOCK")
    out = b""
    for block in blocks:
        lines = [line.strip() for line in block.splitlines()]
        # armor headers ("Version: ...") hold a colon, base64 never does; the checksum line starts with "="
        payload = b"".join(line for line in lines if line and b":" not in line and not line.startswith(b"="))
        try:
            out += base64.b64decode(payload, validate=True)
        except binascii.Error as error:
            raise ValueError(f"the keyring's armor is not base64: {error}") from None
    return out


def folder(sums_url: str) -> str:
    """The folder a checksum list is in, with its trailing slash."""
    return sums_url.split("?", 1)[0].rsplit("/", 1)[0] + "/"


def under(url: str, sums_url: str) -> bool:
    """The file at `url` is in the checksum list's folder or below it."""
    return url.split("?", 1)[0].startswith(folder(sums_url))


def sums_name(url: str, sums_url: str) -> str:
    """The name a checksum list gives the file at `url`: its path relative to the list's folder, or,
    for a file elsewhere, its file name."""
    path = url.split("?", 1)[0]
    name = path[len(folder(sums_url)) :] if under(url, sums_url) else path.rsplit("/", 1)[-1]
    return urllib.parse.unquote(name)


def listed(sums: str, name: str) -> dict[str, str]:
    """{algorithm: lowercase hex} a `sha256sum`-style list gives `name` (`<hex>  <name>` or `<hex> *<name>`).

    Raises ValueError when no line names it, or two lines give it different checksums of one algorithm."""
    found: dict[str, str] = {}
    for line in sums.splitlines():
        match = SUMS_LINE.fullmatch(line.rstrip("\r"))
        if match is None or match.group(2).removeprefix("./") != name:
            continue
        digest = match.group(1).lower()
        algorithm = BY_LENGTH.get(len(digest))
        if algorithm is None:
            raise ValueError(f"the signed list gives {name} a checksum of {len(digest)} hex digits, which is no sha")
        if found.get(algorithm, digest) != digest:
            raise ValueError(f"the signed list gives {name} two different {algorithm} checksums")
        found[algorithm] = digest
    if not found:
        raise ValueError(f"the signed list does not name {name}")
    return found
