"""The records carry their standard types and shapes, and the lock's part of them."""

import base64
import json
import unittest

from input_lock import records
from input_lock.identity import lock_digest
from input_lock.lock import parse

from .test_lock import EXAMPLE_TEXT

HEX = "a" * 64


class Records(unittest.TestCase):
    def test_provenance(self) -> None:
        lock = parse(EXAMPLE_TEXT)
        deps = records.lock_dependencies(lock, ["shellcheck", "jq"])
        self.assertEqual(deps[0], {"name": "lock.json", "digest": {"sha256": lock_digest(lock)}})
        self.assertEqual([d.get("name") for d in deps[1:]], ["jq", "shellcheck"])
        self.assertEqual(set(deps[2]["digest"]), {"sha256", "sha512"})
        self.assertTrue(deps[1]["uri"].endswith("/jq-1.8.2/jq-linux-amd64"))
        predicate = records.provenance(
            "https://example.com/build-type/v1",
            {"image": "builder"},
            deps,
            "https://example.com/ci",
            internal_parameters={"identity": HEX},
            builder_version={"input-lock": "0.1.0"},
            metadata={"startedOn": "2026-11-21T00:00:00Z"},
        )
        statement = records.statement([records.subject("builder.tar", HEX)], records.PROVENANCE_PREDICATE, predicate)
        self.assertEqual(statement["_type"], "https://in-toto.io/Statement/v1")
        self.assertEqual(statement["predicateType"], "https://slsa.dev/provenance/v1")
        self.assertEqual(predicate["buildDefinition"]["internalParameters"], {"identity": HEX})
        self.assertEqual(predicate["runDetails"]["builder"]["version"], {"input-lock": "0.1.0"})
        bare = records.provenance("t", {}, [], "b")
        self.assertNotIn("internalParameters", bare["buildDefinition"])
        self.assertEqual(bare["runDetails"], {"builder": {"id": "b"}, "metadata": {}})

    def test_subjects(self) -> None:
        two = [records.subject("a.tar", HEX), records.subject("b.squashfs", "b" * 64)]
        self.assertEqual(len(records.statement(two, "t", records.test_result("PASSED", []))["subject"]), 2)
        with self.assertRaises(ValueError):
            records.statement([], "t", records.test_result("PASSED", []))
        with self.assertRaises(ValueError):
            records.subject("a", "sha256:" + HEX)
        with self.assertRaises(ValueError):
            records.statement([{"name": "a", "digest": {"sha512": "b" * 128}}], "t", records.test_result("PASSED", []))

    def test_spdx(self) -> None:
        lock = parse(EXAMPLE_TEXT)
        debs = records.deb_packages(
            [("libssl3t64", "3.5.1-1+deb13u2", "amd64"), ("g++", "4:14.2.0-1", "amd64")], "trixie"
        )
        self.assertEqual([p["name"] for p in debs], ["g++", "libssl3t64"])
        self.assertEqual(
            debs[1]["externalRefs"][0]["referenceLocator"],
            "pkg:deb/debian/libssl3t64@3.5.1-1%2Bdeb13u2?arch=amd64&distro=trixie",
        )
        self.assertEqual(debs[0]["SPDXID"], "SPDXRef-Package-g---amd64")
        inputs = records.input_packages(lock, ["shellcheck"])
        self.assertEqual(inputs[0]["SPDXID"], "SPDXRef-Input-shellcheck")
        self.assertEqual([c["algorithm"] for c in inputs[0]["checksums"]], ["SHA256", "SHA512"])
        self.assertTrue(inputs[0]["downloadLocation"].startswith("https://github.com/koalaman/"))
        root: records.SpdxPackage = {
            "SPDXID": "SPDXRef-Image",
            "name": "builder",
            "versionInfo": "sha256:" + HEX,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "primaryPackagePurpose": "CONTAINER",
        }
        document = records.spdx_document(
            "builder", "https://example.com/spdx/x", "2026-11-21T00:00:00Z", "Tool: example-1.0", root, [*debs, *inputs]
        )
        self.assertEqual(document["spdxVersion"], "SPDX-2.3")
        self.assertEqual(document["relationships"][0]["relationshipType"], "DESCRIBES")
        self.assertEqual(sum(r["relationshipType"] == "CONTAINS" for r in document["relationships"]), 3)
        with self.assertRaises(ValueError):
            records.spdx_document("x", "n", "c", "t", root, [debs[0], debs[0]])

    def test_test_result_and_envelopes(self) -> None:
        result = records.test_result("PASSED", [{"name": "boots"}], url="https://example.com/run/1", passed=["boot"])
        self.assertEqual(set(result), {"result", "configuration", "url", "passedTests", "warnedTests", "failedTests"})
        with self.assertRaises(ValueError):
            records.test_result("OK", [])  # type: ignore[arg-type]  # the check under test is the runtime one
        statement = records.statement([records.subject("x", HEX)], records.TEST_RESULT_PREDICATE, result)
        line = records.jsonl([records.envelope(statement)])
        self.assertEqual(line.count("\n"), 1)
        envelope = json.loads(line)
        self.assertEqual(envelope["payloadType"], "application/vnd.in-toto+json")
        self.assertEqual(envelope["signatures"], [])
        self.assertEqual(json.loads(base64.b64decode(envelope["payload"])), statement)
        self.assertEqual(records.asset_name(HEX), HEX + ".intoto.jsonl")
        with self.assertRaises(ValueError):
            records.asset_name("sha256:" + HEX)


if __name__ == "__main__":
    unittest.main()
