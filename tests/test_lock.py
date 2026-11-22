"""The lock format: what passes, what each check catches, the canonical text and build.env."""

import copy
import json
import re
import shlex
import unittest

from input_lock.lock import (
    Lock,
    LockError,
    build_env,
    build_fields,
    dump,
    entries_for,
    env_name,
    loads,
    parse,
    placeholders,
    problems,
    render_url,
    schema_of,
    shell,
)

from . import ROOT

EXAMPLE_TEXT = (ROOT / "examples" / "lock.json").read_text(encoding="utf-8")
HEX = "a" * 64


def example() -> Lock:
    return parse(EXAMPLE_TEXT)


def found(data: object) -> str:
    return " | ".join(problems(json.loads(json.dumps(data))))


class TheExample(unittest.TestCase):
    def test_is_well_formed_and_in_its_own_form(self) -> None:
        self.assertEqual(problems(loads(EXAMPLE_TEXT)), [])
        self.assertEqual(dump(example()), EXAMPLE_TEXT)

    def test_the_readme_shows_it_verbatim(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        first = re.search(r"```json\n(.*?)```", readme, re.DOTALL)
        assert first is not None
        self.assertEqual(first.group(1), EXAMPLE_TEXT, "the README's lock is not examples/lock.json")

    def test_urls_render(self) -> None:
        lock = example()
        self.assertEqual(
            render_url(lock["inputs"]["shellcheck"]),
            "https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz",
        )
        self.assertEqual(
            render_url(lock["inputs"]["alloy"]), "https://apt.grafana.com/pool/main/a/alloy/alloy-1.22.0-1.amd64.deb"
        )


class Schema(unittest.TestCase):
    def test_missing_is_one(self) -> None:
        self.assertEqual(schema_of({"inputs": {}}), 1)
        self.assertEqual(schema_of({"schema": 1, "inputs": {}}), 1)

    def test_a_future_schema_says_to_upgrade(self) -> None:
        with self.assertRaises(LockError) as caught:
            parse('{"schema": 2, "inputs": {}}')
        self.assertIn("schema 2", str(caught.exception))
        self.assertIn("upgrade input-lock", str(caught.exception))
        self.assertEqual(len(problems({"schema": 2, "inputs": "anything"})), 1, "nothing else is judged")

    def test_a_schema_is_a_positive_integer(self) -> None:
        for bad in ("1", True, 0, 1.0, None):
            self.assertIn("positive integer", found({"schema": bad, "inputs": {}}), bad)

    def test_empty_text_is_an_empty_lock(self) -> None:
        self.assertEqual(parse("  \n"), {"inputs": {}})

    def test_not_json(self) -> None:
        with self.assertRaises(LockError):
            parse("{")

    def test_a_repeated_key_is_refused(self) -> None:
        with self.assertRaises(LockError) as caught:
            parse('{"inputs": {"jq": {}, "jq": {}}}')
        self.assertIn("'jq' appears twice", str(caught.exception))


class Checks(unittest.TestCase):
    def entry(self, **fields: object) -> dict[str, object]:
        base: dict[str, object] = {"version": "1.2.3", "url": "https://example.com/t-{version}.tgz", "sha256": HEX}
        base.update(fields)
        return base

    def lock(self, **entries: object) -> dict[str, object]:
        return {"inputs": entries}

    def test_a_minimal_entry_passes(self) -> None:
        self.assertEqual(found(self.lock(tool=self.entry())), "")

    def test_top_level(self) -> None:
        self.assertIn("unknown top-level", found({"inputs": {}, "extra": 1}))
        self.assertIn("snapshot.debian.org", found({"inputs": {}, "debian_snapshot": "yesterday"}))
        self.assertEqual(found({"inputs": {}, "debian_snapshot": "20261121T005332Z"}), "")
        self.assertIn("inputs must be an object", found({"inputs": []}))
        self.assertIn("JSON object", found([]))

    def test_each_check_catches_what_it_is_for(self) -> None:
        cases = {
            "lowercase letters, digits and dashes": {"Tool": self.entry()},
            "not an object": {"tool": "1.2.3"},
            "no version": {"tool": self.entry(version=" ")},
            "no url": {"tool": {"version": "1", "sha256": HEX}},
            "url is not https": {"tool": self.entry(url="http://example.com/{version}")},
            "which the entry does not have": {"tool": self.entry(url="https://example.com/{missing}")},
            "no checksum": {"tool": {"version": "1", "url": "https://example.com/{version}"}},
            "64 lowercase hex": {"tool": self.entry(sha256="ABC")},
            "128 lowercase hex": {"tool": self.entry(sha512="ab")},
            "must be built from it": {"tool": self.entry(url="https://example.com/x", datasource="npm", depName="x")},
            "come together": {"tool": self.entry(datasource="npm")},
            "sorted list of distinct names": {"tool": self.entry(used_by=["b", "a"])},
            "used_by must be a list": {"tool": self.entry(used_by="a")},
            "module must be a name or null": {"tool": self.entry(module=3)},
            "unknown field 'buildId'": {"tool": self.entry(buildId="x")},
            "must be a string (a field of your own": {"tool": self.entry(build=7)},
            "placeholder the version derives": {"tool": self.entry(major="3")},
            "depName must be a string": {"tool": self.entry(datasource="npm", depName=["x"])},
            "an apt entry has datasource deb": {
                "tool": self.entry(apt="tool", url="https://example.com/{version}.deb")
            },
            "an apt entry's url is the .deb": {
                "tool": self.entry(
                    apt="tool",
                    datasource="deb",
                    depName="tool",
                    registryUrl="https://example.com?suite=s&components=main",
                )
            },
            "names its suite and components": {
                "tool": self.entry(datasource="deb", depName="tool", registryUrl="https://example.com/debian")
            },
            "registryUrl is not https": {
                "tool": self.entry(
                    datasource="deb", depName="tool", registryUrl="http://example.com?suite=s&components=main"
                )
            },
        }
        for words, inputs in cases.items():
            with self.subTest(words):
                self.assertIn(words, found({"inputs": inputs}))

    def test_used_by_against_the_consumers_images(self) -> None:
        data = json.loads(json.dumps(self.lock(tool=self.entry(used_by=["a", "b"]))))
        self.assertEqual(problems(data, images=["a", "b", "c"]), [])
        self.assertIn("which are not among", " ".join(problems(data, images=["a"])))
        self.assertEqual(problems(data), [], "without a list, any name goes")

    def test_one_entry_per_apt_package(self) -> None:
        deb = {
            "apt": "pkg",
            "datasource": "deb",
            "depName": "pkg",
            "registryUrl": "https://example.com?suite=s&components=main",
            "url": "https://example.com/pkg_{version}.deb",
        }
        self.assertIn("also inputs.one", found(self.lock(one=self.entry(**deb), two=self.entry(**deb))))

    def test_two_fields_cannot_share_a_build_env_name(self) -> None:
        clash = self.lock(**{"foo": self.entry(bar_url="x"), "foo-bar": self.entry()})
        self.assertIn("LOCK_FOO_BAR_URL", found(clash))

    def test_extension_fields_are_allowed(self) -> None:
        self.assertEqual(found(self.lock(tool=self.entry(build="17", workload_set="10.0.402.1", module=None))), "")


class Urls(unittest.TestCase):
    def test_placeholders(self) -> None:
        entry = {"version": "5:29.1.0+dfsg-1", "url": "https://example.com/{build}", "build": "b7"}
        values = placeholders(entry)
        self.assertEqual(values["version_noepoch"], "29.1.0+dfsg-1")
        self.assertEqual(values["version_underscore"], "5:29.1.0_dfsg-1")
        self.assertEqual(values["version_urlquoted"], "5%3A29.1.0%2Bdfsg-1")
        self.assertEqual(values["major"], "29")
        self.assertEqual(render_url(entry), "https://example.com/b7")

    def test_a_missing_placeholder_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            render_url({"version": "1", "url": "https://example.com/{nope}"})


class BuildFields(unittest.TestCase):
    def test_hints_and_bookkeeping_stay_out_and_the_url_is_rendered(self) -> None:
        entry = example()["inputs"]["jq"]
        fields = build_fields(entry)
        self.assertEqual(
            fields,
            {
                "sha256": entry["sha256"],
                "url": "https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64",
                "version": "1.8.2",
            },
        )

    def test_unknown_and_module_fields_stay_in(self) -> None:
        entry = {"version": "1", "url": "https://example.com/{version}", "sha1": "b" * 40, "module": "x", "sdk": "p;1"}
        self.assertEqual(set(build_fields(entry)), {"version", "url", "sha1", "module", "sdk"})


class PublicApi(unittest.TestCase):
    def test_what_a_consumer_imports_is_public(self) -> None:
        import input_lock
        from input_lock import lock as lock_module

        for name in ("SNAPSHOT_ID", "entry_problems", "entry_env", "build_env", "problems"):
            with self.subTest(name=name):
                self.assertIn(name, input_lock.__all__)
                self.assertIs(getattr(input_lock, name), getattr(lock_module, name))
        self.assertTrue(input_lock.SNAPSHOT_ID.fullmatch("20261121T005332Z"))
        self.assertEqual(
            input_lock.entry_problems("x", {"version": "1"}),
            ["inputs.x: no url", "inputs.x: no checksum (sha256, sha512, sha1)"],
        )


class BuildEnv(unittest.TestCase):
    def test_one_shell_word_per_value(self) -> None:
        lock = example()
        env = dict(build_env(lock))
        self.assertEqual(env["LOCK_ENTRIES"], "alloy jq shellcheck")
        self.assertEqual(env["LOCK_JQ_VERSION"], "1.8.2")
        self.assertNotIn("LOCK_DEBIAN_SNAPSHOT", env, "a lock without a snapshot exports none")
        self.assertNotIn("LOCK_JQ_DEPNAME", env)
        self.assertNotIn("LOCK_JQ_USED_BY", env)
        for line in shell(build_env(lock)).splitlines():
            _, _, value = line.partition("=")
            self.assertEqual(len(shlex.split(value)), 1, line)
            self.assertNotIn("{", value, line)

    def test_an_image_gets_only_its_entries(self) -> None:
        lock = example()
        self.assertEqual(list(entries_for(lock, "builder")), ["jq", "shellcheck"])
        self.assertEqual(dict(build_env(lock, "runner"))["LOCK_ENTRIES"], "alloy jq")
        self.assertEqual(build_env(lock, "nobody"), [("LOCK_ENTRIES", "")])

    def test_the_snapshot_is_exported_when_the_lock_has_one(self) -> None:
        lock = copy.deepcopy(example())
        lock["debian_snapshot"] = "20261121T005332Z"
        self.assertIn(("LOCK_DEBIAN_SNAPSHOT", "20261121T005332Z"), build_env(lock))
        self.assertEqual(env_name("android-system-image-34"), "LOCK_ANDROID_SYSTEM_IMAGE_34")

    def test_the_snapshot_can_be_left_out(self) -> None:
        lock = copy.deepcopy(example())
        lock["debian_snapshot"] = "20261121T005332Z"
        without = build_env(lock, snapshot=False)
        self.assertNotIn("LOCK_DEBIAN_SNAPSHOT", dict(without))
        self.assertEqual(without, build_env(lock)[1:], "only the snapshot line goes")

    def test_quoting(self) -> None:
        self.assertEqual(shell([("A", "it's a value")]), "A='it'\"'\"'s a value'\n")


if __name__ == "__main__":
    unittest.main()
