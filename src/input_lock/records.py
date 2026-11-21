"""Records about what a build made and what it was made from, in the formats verifiers already read.

Each record is an in-toto Statement v1 about one or more artifact files, carried in a DSSE
envelope (unsigned here; whoever publishes signs it):

    provenance    SLSA provenance v1            https://slsa.dev/provenance/v1
    inventory     an SPDX 2.3 JSON document     https://spdx.dev/Document/v2.3
    test result   in-toto test-result v0.1      https://in-toto.io/attestation/test-result/v0.1

A file artifact carries its records beside it as `<sha256 hex>.intoto.jsonl`, one envelope per
line; an OCI image carries each envelope as a referrer of its digest. The lock's part is
`lock_dependencies` (the lock and every locked download, for provenance) and `input_packages`
(every locked download as an SPDX package). What a build type means, which packages an artifact
holds and who signs are the build tool's: these functions only assemble the standard shapes.
Pure functions; no record holds a secret, a key or a machine identity.
"""

import base64
import json
import re
import urllib.parse
from collections.abc import Iterable, Mapping, Sequence
from typing import Final, Literal, NotRequired, TypedDict

from .identity import HEX64, Package, canonical, lock_digest
from .lock import JsonValue, Lock, checksums, render_url, text

STATEMENT_TYPE: Final = "https://in-toto.io/Statement/v1"
PROVENANCE_PREDICATE: Final = "https://slsa.dev/provenance/v1"
SPDX_PREDICATE: Final = "https://spdx.dev/Document/v2.3"
TEST_RESULT_PREDICATE: Final = "https://in-toto.io/attestation/test-result/v0.1"
DSSE_PAYLOAD_TYPE: Final = "application/vnd.in-toto+json"
SPDX_ALGORITHMS: Final[Mapping[str, str]] = {"sha256": "SHA256", "sha512": "SHA512", "sha1": "SHA1"}

type TestOutcome = Literal["PASSED", "WARNED", "FAILED"]


class ResourceDescriptor(TypedDict, total=False):
    """in-toto's ResourceDescriptor: a file or a download, by name and/or URI, with its digests."""

    name: str
    uri: str
    digest: dict[str, str]


class Builder(TypedDict):
    id: str
    version: NotRequired[dict[str, str]]


class BuildMetadata(TypedDict, total=False):
    invocationId: str
    startedOn: str
    finishedOn: str


class BuildDefinition(TypedDict):
    buildType: str
    externalParameters: dict[str, JsonValue]
    internalParameters: NotRequired[dict[str, JsonValue]]
    resolvedDependencies: list[ResourceDescriptor]


class RunDetails(TypedDict):
    builder: Builder
    metadata: BuildMetadata


class Provenance(TypedDict):
    buildDefinition: BuildDefinition
    runDetails: RunDetails


class SpdxChecksum(TypedDict):
    algorithm: str
    checksumValue: str


class SpdxExternalRef(TypedDict):
    referenceCategory: str
    referenceType: str
    referenceLocator: str


class SpdxPackage(TypedDict):
    SPDXID: str
    name: str
    versionInfo: str
    downloadLocation: str
    filesAnalyzed: bool
    checksums: NotRequired[list[SpdxChecksum]]
    externalRefs: NotRequired[list[SpdxExternalRef]]
    primaryPackagePurpose: NotRequired[str]


class SpdxRelationship(TypedDict):
    spdxElementId: str
    relationshipType: str
    relatedSpdxElement: str


class SpdxCreationInfo(TypedDict):
    created: str
    creators: list[str]


class SpdxDocument(TypedDict):
    spdxVersion: str
    dataLicense: str
    SPDXID: str
    name: str
    documentNamespace: str
    creationInfo: SpdxCreationInfo
    packages: list[SpdxPackage]
    relationships: list[SpdxRelationship]


class TestResult(TypedDict):
    result: TestOutcome
    configuration: list[ResourceDescriptor]
    url: str
    passedTests: list[str]
    warnedTests: list[str]
    failedTests: list[str]


type Predicate = Provenance | SpdxDocument | TestResult


class Statement(TypedDict):
    _type: str
    subject: list[ResourceDescriptor]
    predicateType: str
    predicate: Predicate


"""An in-toto Statement v1."""


class Signature(TypedDict):
    sig: str
    keyid: NotRequired[str]


class Envelope(TypedDict):
    """A DSSE envelope: the statement's canonical JSON, base64, and its signatures."""

    payloadType: str
    payload: str
    signatures: list[Signature]


# ---- statements -------------------------------------------------------------------------------


def subject(name: str, sha256: str) -> ResourceDescriptor:
    """One artifact file a statement is about."""
    if not HEX64.fullmatch(sha256):
        raise ValueError(f"subject digest is not sha256 hex: {sha256!r}")
    return {"name": name, "digest": {"sha256": sha256}}


def statement(subjects: Sequence[ResourceDescriptor], predicate_type: str, predicate: Predicate) -> Statement:
    """An in-toto Statement v1 about one or more artifact files (an image split in two, a kit of several)."""
    if not subjects:
        raise ValueError("a statement is about at least one subject")
    for one in subjects:
        if not HEX64.fullmatch(one.get("digest", {}).get("sha256", "")):
            raise ValueError(f"subject {one.get('name')!r} has no sha256 hex digest")
    return {"_type": STATEMENT_TYPE, "subject": list(subjects), "predicateType": predicate_type, "predicate": predicate}


# ---- provenance -------------------------------------------------------------------------------


def lock_dependencies(lock: Lock, names: Iterable[str], lock_name: str = "lock.json") -> list[ResourceDescriptor]:
    """The lock file and each named entry's download, as SLSA resolvedDependencies."""
    found: list[ResourceDescriptor] = [{"name": lock_name, "digest": {"sha256": lock_digest(lock)}}]
    for name in sorted(names):
        entry = lock["inputs"][name]
        found.append({"name": name, "uri": render_url(entry), "digest": checksums(entry)})
    return found


def provenance(
    build_type: str,
    external_parameters: dict[str, JsonValue],
    resolved_dependencies: Sequence[ResourceDescriptor],
    builder_id: str,
    internal_parameters: dict[str, JsonValue] | None = None,
    builder_version: dict[str, str] | None = None,
    metadata: BuildMetadata | None = None,
) -> Provenance:
    """A SLSA provenance v1 predicate. build_type names the document that says what the parameters mean."""
    definition: BuildDefinition = {
        "buildType": build_type,
        "externalParameters": external_parameters,
        "resolvedDependencies": list(resolved_dependencies),
    }
    if internal_parameters:
        definition["internalParameters"] = internal_parameters
    builder: Builder = {"id": builder_id}
    if builder_version:
        builder["version"] = builder_version
    return {"buildDefinition": definition, "runDetails": {"builder": builder, "metadata": metadata or {}}}


# ---- SPDX -------------------------------------------------------------------------------------


def spdx_id(label: str) -> str:
    """An SPDX element ID from any text: SPDXRef- and letters, digits, '.' and '-'."""
    return "SPDXRef-" + re.sub(r"[^A-Za-z0-9.-]", "-", label)


def purl_ref(locator: str) -> SpdxExternalRef:
    return {"referenceCategory": "PACKAGE-MANAGER", "referenceType": "purl", "referenceLocator": locator}


def deb_packages(packages: Iterable[Package], distro: str | None = None) -> list[SpdxPackage]:
    """Installed Debian packages, (name, version, architecture), as SPDX packages with a pkg:deb purl."""
    rows = sorted({(str(name), str(version), str(arch)) for name, version, arch in packages})
    suffix = f"&distro={urllib.parse.quote(distro)}" if distro else ""
    return [
        {
            "SPDXID": spdx_id(f"Package-{name}-{arch}"),
            "name": name,
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "externalRefs": [
                purl_ref(f"pkg:deb/debian/{urllib.parse.quote(name)}@{urllib.parse.quote(version)}?arch={arch}{suffix}")
            ],
        }
        for name, version, arch in rows
    ]


def input_packages(lock: Lock, names: Iterable[str]) -> list[SpdxPackage]:
    """Each named lock entry as an SPDX package: its version, its download and its checksums."""
    found: list[SpdxPackage] = []
    for name in sorted(names):
        entry = lock["inputs"][name]
        version = text(entry, "version")
        found.append(
            {
                "SPDXID": spdx_id(f"Input-{name}"),
                "name": name,
                "versionInfo": version,
                "downloadLocation": render_url(entry),
                "filesAnalyzed": False,
                "checksums": [
                    {"algorithm": SPDX_ALGORITHMS[algorithm], "checksumValue": value}
                    for algorithm, value in checksums(entry).items()
                ],
                "externalRefs": [purl_ref(f"pkg:generic/{urllib.parse.quote(name)}@{urllib.parse.quote(version)}")],
            }
        )
    return found


def spdx_document(
    name: str, namespace: str, created: str, creator: str, root: SpdxPackage, contains: Sequence[SpdxPackage]
) -> SpdxDocument:
    """An SPDX 2.3 document that DESCRIBES `root` (the artifact), which CONTAINS each other package.

    creator: "Tool: <name>-<version>"; created: an RFC 3339 UTC time."""
    ids = [root["SPDXID"], *(package["SPDXID"] for package in contains)]
    if len(set(ids)) != len(ids):
        raise ValueError(f"SPDX IDs repeat: {sorted({i for i in ids if ids.count(i) > 1})}")
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": name,
        "documentNamespace": namespace,
        "creationInfo": {"created": created, "creators": [creator]},
        "packages": [root, *contains],
        "relationships": [
            relationship("SPDXRef-DOCUMENT", "DESCRIBES", root["SPDXID"]),
            *(relationship(root["SPDXID"], "CONTAINS", package["SPDXID"]) for package in contains),
        ],
    }


def relationship(element: str, kind: str, related: str) -> SpdxRelationship:
    return {"spdxElementId": element, "relationshipType": kind, "relatedSpdxElement": related}


# ---- test results -----------------------------------------------------------------------------


def test_result(
    result: TestOutcome,
    configuration: Sequence[ResourceDescriptor],
    url: str = "",
    passed: Iterable[str] = (),
    warned: Iterable[str] = (),
    failed: Iterable[str] = (),
) -> TestResult:
    """An in-toto test-result v0.1 predicate: one check of these exact bits. configuration[0] names the check."""
    if result not in ("PASSED", "WARNED", "FAILED"):
        raise ValueError(f"result must be PASSED, WARNED or FAILED, not {result!r}")
    return {
        "result": result,
        "configuration": list(configuration),
        "url": url,
        "passedTests": list(passed),
        "warnedTests": list(warned),
        "failedTests": list(failed),
    }


# ---- envelopes --------------------------------------------------------------------------------


def envelope(record: Statement, signatures: Sequence[Signature] = ()) -> Envelope:
    """A DSSE envelope carrying a statement; unsigned unless signatures are given."""
    return {
        "payloadType": DSSE_PAYLOAD_TYPE,
        "payload": base64.b64encode(canonical(record)).decode(),
        "signatures": list(signatures),
    }


def jsonl(envelopes: Iterable[Envelope]) -> str:
    """The text of a `<digest>.intoto.jsonl` file: one envelope per line."""
    return "".join(json.dumps(one, sort_keys=True, separators=(",", ":")) + "\n" for one in envelopes)


def asset_name(artifact_sha256: str) -> str:
    """The name of the records file beside an artifact: `<sha256 hex>.intoto.jsonl`."""
    if not HEX64.fullmatch(artifact_sha256):
        raise ValueError(f"not a sha256 hex digest: {artifact_sha256!r}")
    return f"{artifact_sha256}.intoto.jsonl"
