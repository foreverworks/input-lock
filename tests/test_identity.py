"""The input identity: order-free, moved by everything a build reads, and by nothing else."""

import copy
import hashlib
import unittest

from input_lock.identity import canonical, digest, files_digest, input_identity, lock_digest
from input_lock.lock import Value, dump, parse

from .test_lock import EXAMPLE_TEXT

HEX = "a" * 64
PACKAGES = [("bash", "5.2.37-2", "amd64"), ("curl", "8.14.1-2", "amd64"), ("tzdata", "2025b-4", "all")]

# The identity of the example's entries with PACKAGES, recipe HEX and parent "b" * 64. A change to
# this value changes every identity every consumer has published: it needs a new identity tag.
PINNED = "2bc9883cb889bd8e02d383dd617df1944e9990e5dd154d8d06a8a5a98f4e7fab"


def entries() -> dict[str, dict[str, Value]]:
    return copy.deepcopy(parse(EXAMPLE_TEXT)["inputs"])


def identity(changed: dict[str, dict[str, Value]] | None = None) -> str:
    return input_identity(PACKAGES, changed if changed is not None else entries(), HEX, "b" * 64)


def with_field(name: str, field: str, value: Value) -> dict[str, dict[str, Value]]:
    moved = entries()
    moved[name][field] = value
    return moved


def without_field(name: str, field: str) -> dict[str, dict[str, Value]]:
    moved = entries()
    del moved[name][field]
    return moved


class Identity(unittest.TestCase):
    def test_pinned(self) -> None:
        self.assertEqual(identity(), PINNED)

    def test_order_and_repeats_do_not_count(self) -> None:
        base = identity()
        self.assertEqual(input_identity(list(reversed(PACKAGES)), entries(), HEX, "b" * 64), base)
        self.assertEqual(input_identity(PACKAGES + PACKAGES[:1], entries(), HEX, "b" * 64), base)
        self.assertEqual(identity(dict(reversed(list(entries().items())))), base)

    def test_hints_and_bookkeeping_leave_it_alone(self) -> None:
        base = identity()
        cases: list[tuple[str, str, Value]] = [
            ("jq", "extractVersion", "^v(?<version>.+)$"),
            ("jq", "depName", "someone/jq"),
            ("jq", "datasource", "github-tags"),
            ("jq", "versioning", "semver"),
            ("jq", "packageName", "jq"),
            ("alloy", "registryUrl", "https://apt.grafana.com?suite=beta&components=main"),
            ("jq", "used_by", ["builder", "runner", "third"]),
            ("jq", "description", "for the build scripts"),
        ]
        for name, field, value in cases:
            with self.subTest(f"{name}.{field}"):
                self.assertEqual(identity(with_field(name, field, value)), base)
        self.assertEqual(identity(without_field("jq", "used_by")), base)

    def test_what_a_build_reads_moves_it(self) -> None:
        base = identity()
        moved = {
            "version": with_field("jq", "version", "1.8.3"),
            "url": with_field("jq", "url", "https://mirror.example.com/jq-{version}"),
            "checksum": with_field("jq", "sha256", "c" * 64),
            "another checksum": with_field("jq", "sha1", "d" * 40),
            "module": with_field("jq", "module", "tools"),
            "an unknown field": with_field("jq", "build", "2"),
            "apt": with_field("alloy", "apt", "alloy-beta"),
            "an entry gone": {k: v for k, v in entries().items() if k != "jq"},
        }
        values = {what: identity(changed) for what, changed in moved.items()}
        for what, value in values.items():
            with self.subTest(what):
                self.assertNotEqual(value, base)
        self.assertEqual(len(set(values.values())), len(values), "two different changes gave one identity")

    def test_a_template_that_renders_the_same_url_is_the_same_input(self) -> None:
        literal = with_field("jq", "url", "https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64")
        self.assertEqual(identity(literal), identity())

    def test_packages_recipe_and_parent_move_it(self) -> None:
        base = identity()
        others = {
            input_identity([*PACKAGES[:2], ("tzdata", "2025c-1", "all")], entries(), HEX, "b" * 64),
            input_identity([*PACKAGES[:2], ("tzdata", "2025b-4", "amd64")], entries(), HEX, "b" * 64),
            input_identity(PACKAGES, entries(), "c" * 64, "b" * 64),
            input_identity(PACKAGES, entries(), None, "b" * 64),
            input_identity(PACKAGES, entries(), HEX, "c" * 64),
            input_identity(PACKAGES, entries(), HEX, None),
        }
        self.assertEqual(len(others), 6)
        self.assertNotIn(base, others)

    def test_digests_are_checked(self) -> None:
        with self.assertRaises(ValueError):
            input_identity(PACKAGES, entries(), "short", None)
        with self.assertRaises(ValueError):
            input_identity(PACKAGES, entries(), HEX, "B" * 64)

    def test_never_equal_to_a_whole_entry_digest(self) -> None:
        old = digest(
            {
                "identity": 1,
                "packages": [list(p) for p in sorted(PACKAGES)],
                "lock": entries(),
                "recipe": HEX,
                "parent": "b" * 64,
            }
        )
        self.assertNotEqual(old, identity())


class Digests(unittest.TestCase):
    def test_canonical(self) -> None:
        self.assertEqual(canonical({"b": 1, "a": [1, "é"]}), '{"a":[1,"é"],"b":1}'.encode())

    def test_files_digest_is_order_free(self) -> None:
        one = files_digest({"build.sh": b"one", "lock.sh": b"two"})
        self.assertEqual(files_digest({"lock.sh": b"two", "build.sh": b"one"}), one)
        self.assertNotEqual(files_digest({"lock.sh": b"two", "build.sh": b"one!"}), one)
        self.assertNotEqual(files_digest({"lock.sh": b"two", "other.sh": b"one"}), one)

    def test_lock_digest_is_the_files_sha256(self) -> None:
        lock = parse(EXAMPLE_TEXT)
        self.assertEqual(lock_digest(lock), hashlib.sha256(EXAMPLE_TEXT.encode()).hexdigest())
        self.assertEqual(dump(lock), EXAMPLE_TEXT)


if __name__ == "__main__":
    unittest.main()
