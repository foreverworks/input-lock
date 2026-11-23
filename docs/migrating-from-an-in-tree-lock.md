# Migrating from an in-tree lock

input-lock started as a lock file and a few scripts inside one repository. If your repository has
grown the same thing (a JSON file of pinned downloads, a script that validates it, a script that
exports it to a build, a workflow that fixes checksums on Renovate branches), this guide swaps
those for a pinned release of input-lock, one piece at a time, without a flag day.

The order matters only in one place: the identity (step 6) changes every digest once, so do it
when a full rebuild is acceptable.

## 1. Make the file a schema 1 lock

Most in-tree locks already are one. Check yours:

```sh
input-lock check --lock path/to/lock.json
```

Typical findings, and the fix:

| Finding | Fix |
|---|---|
| unknown top-level fields | move them into an entry, or into a file of their own |
| an unknown field that is not lowercase | it is probably a misspelt Renovate hint; otherwise rename it to `snake_case` |
| a field of your own that is not a string | make it a string (a build reads strings) |
| two fields with one build.env name | rename one entry or field |

Add `"schema": 1` at the top while you are there. It is optional (a lock without it is schema 1),
but it costs one line and makes the file say which format it is.

## 2. Keep your own rules as a thin layer

input-lock checks what is true for every lock. Rules about your repository (a closed list of
module names, a list of images that `used_by` may name, a version that must match another one)
stay yours, on top of the library:

```python
from input_lock import problems, loads

IMAGES = ("base", "runner", "desktop")

def my_problems(text: str) -> list[str]:
    data = loads(text)
    found = problems(data, images=IMAGES)       # every generic rule, used_by against your images
    if not found:
        for name, entry in data["inputs"].items():
            if "used_by" not in entry:
                found.append(f"inputs.{name}: every entry here says who installs it")
    return found
```

Delete the generic half of your validator. What is left is the part only your repository knows.

## 3. Export build.env with `input-lock env`

```sh
input-lock env --lock lock.json --image runner > build.env
input-lock sh > lock.sh
```

Your build scripts keep reading `LOCK_<ENTRY>_<FIELD>`. Two differences from a hand-written
exporter are likely:

- input-lock exports every field a build reads, not a fixed list: `module` when it is a string,
  and every field of your own. If your CI compares build.env before and after a push to decide
  what to rebuild, this adds lines once, so everything rebuilds once.
- `LOCK_DEBIAN_SNAPSHOT` is exported only when the lock has a `debian_snapshot`.

Anything your builds need that is not a lock field (a list derived from several entries, say)
stays in your own exporter, appended after input-lock's output.

## 4. Replace the checksum workflow with the Action

```yaml
name: lock checksums
on:
  push:
    branches: ["renovate/**"]
permissions:
  contents: write
jobs:
  checksums:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0
      - uses: foreverworks/input-lock@d2234a6d93e4e5dcf7b5472335e6acc4c2ac9777 # v0.2.0
        with:
          lock: path/to/lock.json
```

If the same workflow also rewrites checksums in other files (pins in YAML variables, say), keep
your script for those files only, run the Action with `commit: "false"`, and commit both in your
own step. Renovate keeps managing the branch as long as the commit's author is in its
`gitIgnoredAuthors` (the preset lists `github-actions[bot]`).

On a self-hosted runner without the GitHub Python builds, pass `python: python3` (3.13 or newer).

## 5. Replace the custom manager with the preset

In `renovate.json`, delete your `customType: "jsonata"` manager for the lock and extend the preset:

```json
{
  "extends": ["github>foreverworks/input-lock#v0.2.0"]
}
```

The preset reads any file named `lock.json`. If yours has another name, keep your own manager
with the preset's `matchStrings` and leave the preset out. Your `packageRules` (groups,
`allowedVersions`, approvals) do not change: they match on datasource and package name, which
are the same.

## 6. Switch the identity

If you compute an identity over lock entries, replace it with `input_lock.input_identity`. It
hashes `build_fields(entry)`, the entry without Renovate's hints and the bookkeeping, and is
tagged `"identity": 2`, so it never equals a digest of whole entries. Every identity changes once.
After that, a Renovate hint or another image added to `used_by` no longer rebuilds anything.

If you publish artifacts under their identity, switch when a full rebuild is fine anyway (a new
base snapshot, say), or record both identities for one cycle.

## 7. Pin input-lock itself in your lock

The tool your build trusts with its downloads is a download too. Give it an entry:

```json
"input-lock": {
  "datasource": "github-releases",
  "depName": "foreverworks/input-lock",
  "extractVersion": "^v(?<version>.+)$",
  "sha256": "<sha256 of input-lock-0.2.0.pyz>",
  "url": "https://github.com/foreverworks/input-lock/releases/download/v{version}/input-lock-{version}.pyz",
  "version": "0.2.0"
}
```

Renovate moves it like any other entry, and the Action fills its checksum. To run it, read the
entry with plain Python and check the file before the first use:

```sh
entry() { python3 -I -c 'import json, sys; e = json.load(open(sys.argv[1]))["inputs"]["input-lock"]; print(e["url"].replace("{version}", e["version"]) if sys.argv[2] == "url" else e[sys.argv[2]])' lock.json "$1"; }
curl -fsSL "$(entry url)" -o input-lock.pyz
echo "$(entry sha256)  input-lock.pyz" | sha256sum -c -
python3 input-lock.pyz check --lock lock.json
```

The release asset is reproducible (`scripts/build-pyz` at the tag rebuilds it byte for byte), so
the checksum can be checked against source, not only against the release.

The Action is pinned separately, by commit SHA in your workflow, and Renovate's `github-actions`
manager moves that pin. To keep the two in one pull request, group them:

```json
{
  "packageRules": [
    { "matchPackageNames": ["foreverworks/input-lock"], "groupName": "input-lock" }
  ]
}
```

## 8. Delete the in-tree copies

Once the steps above are on your main branch: delete the old validator's generic half, the
exporter, the checksum script's lock half, the custom manager and their tests. The repository
keeps its lock file, its own rules, and one pinned tool.
