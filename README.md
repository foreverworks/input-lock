# input-lock

**One lock file for every file your build downloads.** Each tarball, binary and vendor `.deb`
gets one entry: its version, where Renovate looks for the next one, the URL built from that
version, and its checksums. Renovate moves the version; a GitHub Action commits the new checksum
to the same branch; your build downloads exactly what the lock says and nothing else. And one
digest, the input identity, tells you whether anything a build reads has changed, so you rebuild
only when it has.

Python standard library only. Typed. MIT.

## The problem

Every build that downloads something pins it somewhere: a version in a Dockerfile, a URL in a
shell script, a checksum in a YAML file three directories away. Then:

- **Pins drift.** The same tool is pinned in four places at three versions, and nobody notices
  until two machines disagree.
- **Checksums go stale on every bump.** Renovate can move a version, but it cannot recompute the
  sha256 of the file that version downloads. Every bump is a pull request that fails its own build,
  or a checksum someone copies from a web page and hopes.
- **Nobody can say what a build really used.** Which exact files went into last Tuesday's image?
  Did this rebuild change anything, or did it just run again? Without one record of the inputs,
  the answer is archaeology.

## What you get

- **A lock file**, `lock.json`, with one entry per download, in a form that diffs cleanly and
  that `input-lock check` validates (URL built from the version, real hex checksums, HTTPS only).
- **A Renovate preset** that reads every entry with a `datasource` and opens pull requests for new
  versions: GitHub releases, npm, Node.js, .NET, a vendor's apt repository, anything Renovate knows.
- **A GitHub Action** that, on Renovate's branch, downloads each moved file (or, for a `.deb`, reads the checksum
  the vendor's `Packages` index publishes), writes the new checksums and commits them. Merge once
  that commit is there.
- **Exactly-what-is-locked downloads**: `input-lock env` writes a `build.env`, and `lock.sh`'s
  `lock_fetch` downloads and verifies each file; `lock_debs` installs a vendor's `.deb` at its
  locked version, never "the repository's newest".
- **The input identity**: a digest of every field a build reads, so a CI cache key or an image tag
  moves when, and only when, an input moves.
- **Records**: SLSA provenance, an SPDX 2.3 inventory and in-toto test results, with the lock's
  part (every locked download and its checksum) filled in.

## Five-minute quick start

**1. Install.** One file, any Python 3.13 or newer:

```sh
curl -fsSLO https://github.com/foreverworks/input-lock/releases/download/v0.1.0/input-lock-0.1.0.pyz
python3 input-lock-0.1.0.pyz --version
```

or `pip install git+https://github.com/foreverworks/input-lock@v0.1.0`, which installs the
`input-lock` command and the `input_lock` library.

**2. Write a lock.** This one is real ([examples/lock.json](examples/lock.json)): jq and
ShellCheck from their GitHub releases, and Grafana Alloy from Grafana's apt repository.

```json
{
  "schema": 1,
  "inputs": {
    "alloy": {
      "apt": "alloy",
      "datasource": "deb",
      "depName": "alloy",
      "registryUrl": "https://apt.grafana.com?suite=stable&components=main&binaryArch=amd64",
      "sha256": "29ca37327c02e30ff7cc3a1e0ff3779217f26092a1adc29ff4738f6a9e4370e3",
      "url": "https://apt.grafana.com/pool/main/a/alloy/alloy-{version}.amd64.deb",
      "used_by": ["runner"],
      "version": "1.22.0-1"
    },
    "jq": {
      "datasource": "github-releases",
      "depName": "jqlang/jq",
      "extractVersion": "^jq-(?<version>.+)$",
      "sha256": "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f",
      "url": "https://github.com/jqlang/jq/releases/download/jq-{version}/jq-linux-amd64",
      "used_by": ["builder", "runner"],
      "version": "1.8.2"
    },
    "shellcheck": {
      "datasource": "github-releases",
      "depName": "koalaman/shellcheck",
      "extractVersion": "^v(?<version>.+)$",
      "sha256": "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198",
      "sha512": "25df28923d7d89cfdb9fa8aeef762a887f2bd4ebfbcd43ae71068c60e9458d66250b0f8d6fd66b4bc03723e6cdd47983c547e3ab4f3ea2cbc4bc4028842cc7b9",
      "url": "https://github.com/koalaman/shellcheck/releases/download/v{version}/shellcheck-v{version}.linux.x86_64.tar.xz",
      "used_by": ["builder"],
      "version": "0.11.0"
    }
  }
}
```

**3. Check it, and prove the checksums.**

```sh
$ input-lock check                       # exit 1 naming every problem; silent when it is right
$ input-lock verify --entry jq --entry shellcheck
ok       jq: sha256 of https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64
ok       shellcheck: sha256 of https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz
ok       shellcheck: sha512 of https://github.com/koalaman/shellcheck/releases/download/v0.11.0/shellcheck-v0.11.0.linux.x86_64.tar.xz
$ input-lock get jq
https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64
```

**4. Download in your build exactly what the lock says.**

```sh
input-lock env --image builder > build.env     # LOCK_JQ_URL=..., LOCK_JQ_SHA256=..., ...
input-lock sh > lock.sh
```

```bash
# in the build (a Dockerfile RUN, a chroot, a CI step): bash, curl and coreutils are enough
. ./build.env && . ./lock.sh
lock_fetch jq /usr/local/bin/jq && chmod +x /usr/local/bin/jq
lock_fetch shellcheck /tmp/shellcheck.tar.xz
```

`lock_fetch` fails, naming the entry, if a byte differs from what the lock pins.

**5. Let Renovate move the versions.** In `renovate.json`:

```json
{
  "extends": ["config:recommended", "github>foreverworks/input-lock#v0.1.0"]
}
```

Renovate now opens a pull request when jq, ShellCheck or Alloy publishes a new version, editing
only `version`.

**6. Let the Action write the checksums.** In `.github/workflows/lock-checksums.yml`:

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
          fetch-depth: 0        # the Action compares the branch with its merge base
      - uses: foreverworks/input-lock@<commit sha of v0.1.0> # v0.1.0
```

A few minutes after Renovate's push, a second commit on the branch carries the new checksums,
with a line per checksum saying where it came from. The preset tells Renovate to ignore that
commit's author, so Renovate keeps rebasing the branch; a rebase runs the Action again.

Inputs: `lock` (default `lock.json`), `base` (default `origin/<default branch>`), `python` (an
interpreter to use instead of installing one), `commit` (`"false"` to only write the file),
`commit-message`. Output: `changed`, the number of checksums rewritten.

The Action commits on `push` events only; on any other event it writes the file and warns. A
commit pushed with the workflow's own token does not start other workflows. If your pull
request's required checks must run on the checksum commit, give `actions/checkout` a token of a
GitHub App or a bot account (`with: token: ...`).

## How the identity works

```python
from input_lock import parse, entries_for, input_identity

lock = parse(open("lock.json").read())
identity = input_identity(
    packages=[("bash", "5.2.37-2", "amd64")],      # what the build installs from its distribution
    entries=entries_for(lock, "builder"),           # the lock entries it installs itself
    recipe_digest=None,                             # sha256 of its own recipe, if it has one
    parent_identity=None,                           # the identity of what it builds on, if anything
)
```

The identity is the sha256 of a canonical JSON document of those four things. From each lock
entry it takes every field **except** Renovate's lookup (`datasource`, `depName`, `packageName`,
`extractVersion`, `versioning`, `registryUrl`) and the bookkeeping (`used_by`, `description`),
with the URL rendered. So:

| You change | The identity |
|---|---|
| a version, a URL, a checksum, `apt`, `module`, any field of your own | moves |
| how Renovate looks up versions, which images use an entry, a description | stays |
| the order of keys, entries or packages, the file's layout | stays |

It is a deny-list by design: a field the library does not know might change what a build makes,
so it counts, and a forgotten field means one rebuild too many, never a stale artifact.
`input-lock identity --image builder` prints it for the lock part alone, ready for a cache key
(add `debian_snapshot` to the key yourself if the job installs from it).
The full definition is in [docs/format.md](docs/format.md#the-input-identity).

## Records

`input_lock.records` assembles the standard records a verifier reads, keyed by the artifact's
digest: an in-toto Statement v1 about one or more files, carrying SLSA provenance v1, an SPDX 2.3
document or an in-toto test result, in a DSSE envelope, one per line of `<sha256>.intoto.jsonl`.
The lock's part is done for you: `lock_dependencies(lock, names)` lists the lock and every
download with its checksums for provenance, and `input_packages(lock, names)` lists them as SPDX
packages. Signing is left to the publisher (cosign, a KMS); the envelopes come unsigned.

## Reference

- [docs/format.md](docs/format.md): every field, every rule, build.env, the identity, the library
- [docs/migrating-from-an-in-tree-lock.md](docs/migrating-from-an-in-tree-lock.md): replacing a
  home-grown lock, validator and checksum workflow with a pinned release

Commands (`--lock <file>` on each, default `lock.json`, `-` for stdin):

| Command | Does |
|---|---|
| `check [--image NAME]...` | validate; exit 1 naming every problem |
| `format [--check]` | rewrite in the file's own form, or check it is |
| `get ENTRY [FIELD]` | print a field; `url` (the default) comes rendered |
| `env [--image NAME]` | print a build.env |
| `sh` | print `lock.sh`, the shell helpers |
| `identity [--image NAME \| --entry NAME...]` | print the input identity of those entries |
| `verify [--entry NAME]...` | download and compare every checksum |
| `refresh --base REF \| --entry NAME...` | rewrite the checksums of moved (or named) entries |

## Status

Version 0.1.0, the first release. The lock format is schema 1 and is meant to stay readable by
every later release; a lock from a newer schema fails with a clear message instead of a guess.
The library and commands may still change before 1.0, and the changelog will say how.

Known limits: a vendor's `Packages` index is trusted over HTTPS, not checked against the vendor's
signing key; one entry pins one file for one architecture (several architectures per entry would
be schema 2). Every push runs the tests on the newest stable Python and checks types and lint against 3.13,
the oldest supported (Debian 13's); every supported version runs weekly and before each release.

## License

MIT. See [LICENSE](LICENSE).
