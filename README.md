# input-lock

**One lock file for every file your build downloads.** Each tarball, binary and vendor `.deb`
gets one entry: its version, where Renovate looks for the next one, the URL built from that
version, and its checksums. Renovate moves the version; a GitHub Action commits the new checksum
to the same branch; your build downloads exactly what the lock says and nothing else. And one
digest, the input identity, tells you whether anything a build reads has changed, so you rebuild
only when it has.

For teams that build images or CI environments from tarballs, binaries and vendor `.deb`s that no
package manager pins for them.

You need Python 3.13 or newer to run it, and nothing to install beyond that: it uses only the standard
library, and it is free (MIT). The download helpers in `lock.sh` need bash, curl and coreutils. The
Action and the Renovate preset need a GitHub repository with Renovate. Checking a vendor's signature
(see [Signed checksums from the vendor](#signed-checksums-from-the-vendor)) also needs `gpgv`.

## The problem

Every build that downloads something pins it somewhere: a version in a Dockerfile, a URL in a
shell script, a checksum in a YAML file three directories away. Then:

- **Pins drift.** The same tool is pinned in four places at three versions, and nobody notices
  until two machines disagree.
- **Checksums go stale on every bump.** Renovate can move a version, but it cannot recompute the
  sha256 of the file that version downloads. Every bump is a pull request that fails its own build,
  or a checksum someone copies from a web page and hopes.
- **Nobody can say what a build really used.** Which exact files went into last Tuesday's image?
  Did this rebuild change anything, or did it only run again? Without one record of the inputs,
  the answer is archaeology.

## Why not Renovate regex managers plus a script?

You can: a regex manager moves a version, and a script of yours recomputes the checksum. Then the
version sits in one file, the URL in another and the checksum behind a script you now maintain,
test and keep in step with every new kind of download. input-lock is that script, written once:
one entry holds the version, the URL built from it and the checksums, so they cannot disagree;
the Action recomputes them on Renovate's branch (for a `.deb`, from the vendor's index, without
the download); `check` and `verify` refuse a lock that is malformed or no longer matches what it
downloads; and the input identity gives you a cache key for free. If you pin one or two files, a
regex manager and a three-line script are enough.

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
curl -fsSLO https://github.com/foreverworks/input-lock/releases/download/v0.3.0/input-lock-0.3.0.pyz
python3 input-lock-0.3.0.pyz --version
```

or `pip install git+https://github.com/foreverworks/input-lock@v0.3.0`, which installs the
`input-lock` command and the `input_lock` library. The commands below say `input-lock`; with the
`.pyz`, run `python3 input-lock-0.3.0.pyz` in its place.

**2. Write a lock.** Save this as `lock.json` ([examples/lock.json](examples/lock.json)): jq and
ShellCheck from their GitHub releases, and Grafana Alloy from Grafana's apt repository. It is
exactly what `input-lock format` writes, so keep it that way: `refresh` refuses a lock that is not.

```json
{
  "inputs": {
    "alloy": {
      "apt": "alloy",
      "datasource": "deb",
      "depName": "alloy",
      "registryUrl": "https://apt.grafana.com?suite=stable&components=main&binaryArch=amd64",
      "sha256": "29ca37327c02e30ff7cc3a1e0ff3779217f26092a1adc29ff4738f6a9e4370e3",
      "url": "https://apt.grafana.com/pool/main/a/alloy/alloy-{version}.amd64.deb",
      "used_by": [
        "runner"
      ],
      "version": "1.22.0-1"
    },
    "jq": {
      "datasource": "github-releases",
      "depName": "jqlang/jq",
      "extractVersion": "^jq-(?<version>.+)$",
      "sha256": "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f",
      "url": "https://github.com/jqlang/jq/releases/download/jq-{version}/jq-linux-amd64",
      "used_by": [
        "builder",
        "runner"
      ],
      "version": "1.8.2"
    },
    "shellcheck": {
      "datasource": "github-releases",
      "depName": "koalaman/shellcheck",
      "extractVersion": "^v(?<version>.+)$",
      "sha256": "8c3be12b05d5c177a04c29e3c78ce89ac86f1595681cab149b65b97c4e227198",
      "sha512": "25df28923d7d89cfdb9fa8aeef762a887f2bd4ebfbcd43ae71068c60e9458d66250b0f8d6fd66b4bc03723e6cdd47983c547e3ab4f3ea2cbc4bc4028842cc7b9",
      "url": "https://github.com/koalaman/shellcheck/releases/download/v{version}/shellcheck-v{version}.linux.x86_64.tar.xz",
      "used_by": [
        "builder"
      ],
      "version": "0.11.0"
    }
  },
  "schema": 1
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
  "extends": ["config:recommended", "github>foreverworks/input-lock#v0.3.0"]
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
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          fetch-depth: 0        # the Action compares the branch with its merge base
      - uses: foreverworks/input-lock@d2234a6d93e4e5dcf7b5472335e6acc4c2ac9777 # v0.2.0
```

A few minutes after Renovate's push, a second commit on the branch carries the new checksums,
with a line per checksum saying where it came from. The preset tells Renovate to ignore that
commit's author, so Renovate keeps rebasing the branch; a rebase runs the Action again.

The lock must already be in its own form (`input-lock format`): the Action, like `refresh`, fails
on one that is not, so the checksum commit changes checksums and nothing else.

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
`extractVersion`, `versioning`, `registryUrl`), the bookkeeping (`used_by`, `description`) and
the trust (`signed_by`, `sums_url`, `sums_sig_url`), with the URL rendered. So:

| You change | The identity |
|---|---|
| a version, a URL, a checksum, `apt`, `module`, any field of your own | moves |
| how Renovate looks up versions, which images use an entry, a description, how a checksum is vouched for (`signed_by`, `sums_url`, `sums_sig_url`) | stays |
| the order of keys, entries or packages, the file's layout | stays |

It is a deny-list by design: a field the library does not know might change what a build makes,
so it counts, and a forgotten field means one rebuild too many, never a stale artifact.
`input-lock identity --image builder` prints it for the lock part alone, ready for a cache key
(add the Debian snapshot to the key yourself if the job installs from it).
The full definition is in [docs/format.md](docs/format.md#the-input-identity).

## Signed checksums from the vendor

By default a checksum is trust on first use: the hash of what HTTPS served when the version moved
(see Known limits). Many vendors sign what they publish, and an entry can ask for that signature
to be checked instead, so a checksum comes from the vendor's own signed list and nothing unsigned
reaches the lock.

```json
"node": {
  "datasource": "node-version",
  "depName": "node",
  "sha256": "5c063e46bab0fbc1c17cc445d7ff129cbdcd0471be6ed83b6ab864f8143bc864",
  "signed_by": "node-release-keys",
  "sums_url": "https://nodejs.org/dist/v{version}/SHASUMS256.txt.asc",
  "url": "https://nodejs.org/dist/v{version}/node-v{version}-linux-x64.tar.xz",
  "version": "26.13.1"
},
"node-release-keys": {
  "description": "The Node.js release keys: a trust root, moved by hand.",
  "sha256": "140f2ad5260fd62773b6243ce8e1d3009645d558f121b8262c55e383dc285932",
  "url": "https://raw.githubusercontent.com/nodejs/release-keys/{version}/gpg-only-active-keys/pubring.kbx",
  "version": "481637f813e912c4aa3622d7964ab426c97b8e8d"
}
```

[examples/signed.json](examples/signed.json) has this pair, and Grafana's apt repository signed
the same way; a weekly CI run checks both against the vendors' own signatures.

- **`signed_by`** names another entry of the same lock: the vendor's public keys, an ordinary
  download pinned by its checksum. A key change is therefore a diff someone reviews, never
  something a server decides. The keyring entry carries no `signed_by` of its own and is moved by
  hand, with its new checksum written in the same change.
- **An apt entry** needs nothing more. `refresh` and `verify` read the repository's `InRelease`
  (or `Release` with `Release.gpg`), check its signature, check that it is the suite the
  `registryUrl` names and that it is not past its `Valid-Until`, and accept the `Packages` index
  only when its sha256 is the one that signed file lists.
- **Any other entry** names the vendor's signed checksum list in **`sums_url`**, a URL template
  like `url`, in `sha256sum` form (`<hex>  <file>`): clear-signed, as Node.js's
  `SHASUMS256.txt.asc` is, or with **`sums_sig_url`** for a detached signature, as HashiCorp's
  `_SHA256SUMS.sig` is. `refresh` takes the checksum from the line that names the entry's file,
  without a download; when the entry also carries an algorithm the list lacks, the download is
  hashed and must agree with the list.

The signature is checked by `gpgv`, GnuPG's verify-only tool (Debian's `gpgv` package; GitHub's
Ubuntu runners have it). input-lock carries no OpenPGP code and adds no dependency: when an entry
has `signed_by` and `gpgv` is not installed, `refresh` and `verify` fail for that entry, naming
the missing tool, rather than fall back to trusting HTTPS. A signature passes when `gpgv` exits 0
and reports a valid signature by a key of the pinned keyring, and no bad, expired or revoked one.
Only the text `gpgv` says is signed is read; a clear-signed file with anything around its one
signed message is refused. The keyring may be binary (a `.gpg` keyring or a `.kbx` keybox) or
ASCII-armored (a vendor's `gpg.key`), which input-lock unwraps first, and is pinned by sha256 or
sha512. It must hold every key the vendor signs one file with: a second signature by a key it
lacks fails the check, as a bad one does. Tested with gpgv 2.4.

A `Release` without `Valid-Until` has no freshness check: an older signed `Release` of the same
suite would still pass. That cannot move an entry to a version the vendor never signed, since the
version is pinned and must be in the index, but it is the vendor's choice to bound, not ours.

`check` needs no `gpgv` and no network: it checks that the fields fit together (`signed_by` names
an entry, `sums_url` and `sums_sig_url` are https templates, an apt entry takes no `sums_url`).
These fields say how a checksum was vouched for, not what a build downloads, so like Renovate's
hints they stay out of build.env and the input identity.

Which vendors this reaches: every apt repository signs its `Release` (the `deb` datasource), and
so do Node.js (`node-version`) and many projects that attach a signed `SHA256SUMS` to their GitHub
releases (`github-releases`). The npm registry's signatures and Sigstore attestations are not
OpenPGP and are not covered; such entries stay trust on first use.

**A lock that uses these fields is schema 2.** input-lock 0.1.0 reads schema 1 only, so it
refuses such a lock ("upgrade input-lock to read it") instead of reading the fields as plain ones
and checking nothing. `input-lock format` writes `"schema": 2` exactly when an entry uses one of
them, and `1` again when none does; `check` refuses a lock that uses them without it. A lock with
no signature fields stays schema 1, readable by every release.

## Records

`input_lock.records` assembles the standard records a verifier reads, keyed by the artifact's
digest: an in-toto Statement v1 about one or more files, carrying SLSA provenance v1, an SPDX 2.3
document or an in-toto test result, in a DSSE envelope, one per line of `<sha256>.intoto.jsonl`.
The lock's part is done for you: `lock_dependencies(lock, names)` lists the lock and every
download with its checksums for provenance, and `input_packages(lock, names)` lists them as SPDX
packages. Signing is left to the publisher (cosign, a KMS); the envelopes come unsigned.

## The library

Everything the commands do is a function in `input_lock`, for a build tool that reads the lock
instead of re-implementing it. What `input_lock.__all__` lists is the public API, and a release
that changes it says so:

| Name | Does |
|---|---|
| `parse(text, images=None)`, `loads(text)`, `dump(lock)`, `schema_of(data)`, `schema_needed(data)` | read, check and write the file; the schema a lock has, and the lowest one that can hold it |
| `problems(data, images=None)`, `entry_problems(name, entry, images=None)` | every problem of a lock, or of one entry, as a sentence, for your own checks to extend |
| `render_url(entry)`, `placeholders(entry)`, `checksums(entry)`, `used_by(entry)` | what an entry says |
| `build_fields(entry)`, `entries_for(lock, image)`, `entry_env(name, entry)`, `env_name(name)` | what a build reads |
| `build_env(lock, image=None, snapshot=True)`, `shell(pairs)` | a build.env; `snapshot=False` leaves the Debian snapshots out, for an environment (a hook's) that must not see the build snapshot |
| `input_identity(...)`, `files_digest(files)`, `lock_digest(lock)`, `digest(value)`, `canonical(value)` | identities and digests |
| `SNAPSHOT_ID` | the pattern of a snapshot.debian.org ID, for a field of your own that holds one |
| `SCHEMA`, `IDENTITY`, `CHECKSUMS`, `HINTS`, `BOOKKEEPING`, `TRUST`, `NOT_BUILD_FIELDS` | the format's constants: the highest schema read, the identity's tag, and the field lists |
| `Lock`, `Entry`, `Value`, `JsonValue`, `LockError`, `Package` | the types |

The modules `input_lock.records` (the records above), `input_lock.fetch` (the network),
`input_lock.deb` (a vendor's apt repository) and `input_lock.refresh` (the refresh plan) are
public too. Anything else is internal and may move in any release.

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
| `env [--image NAME] [--no-snapshot]` | print a build.env |
| `sh` | print `lock.sh`, the shell helpers |
| `identity [--image NAME \| --entry NAME...]` | print the input identity of those entries |
| `verify [--entry NAME]...` | download and compare every checksum |
| `refresh --base REF \| --entry NAME...` | rewrite the checksums of moved (or named) entries; refuses a lock not in its own form |

## When something fails

Every message names the entry or file it is about. These are the ones people meet, and what to do:

| You see | What it means, and what to do |
|---|---|
| ``not in its own form: run `input-lock format` first`` | `refresh` and the Action change checksums and nothing else, so they refuse a lock whose text is not what `format` writes. Run `input-lock format`, commit, and push again. |
| `git merge-base origin/main HEAD: ... no common ancestor (a shallow checkout?...)` | The Action compares the branch with its merge base, which a shallow clone lacks. Give `actions/checkout` `fetch-depth: 0`. Locally, fetch the base branch first. |
| `refresh needs --base <git ref> ... or --entry <name>` | Say which entries moved: `--base origin/main`, or name them with `--entry`. |
| `error jq: HTTP Error 404: Not Found` | The URL built from the new version does not exist. Run `input-lock get jq` to see it, then check the version Renovate chose and the entry's `url` and `extractVersion`. |
| `<name> <version> is not in <index url>` | A vendor's apt index does not list that version (yet), or the `registryUrl` is wrong. |
| `nothing written, 1 of 3 moved entries failed` | `refresh` writes all of the checksums or none. Fix the entries named above it and run it again. |
| `mismatch jq: sha256 of <url>` (from `verify`) | The file the URL serves is no longer the one the lock pins. Do not edit the checksum to make it pass: find out why the vendor's file changed. |
| `<url> hashes to sha256 ..., <list> says ...` | The download disagrees with what the vendor's index or signed list publishes. Treat it as a failed check, not a typo. |
| `gpgv is not installed, and this entry's signature must be checked` | An entry with `signed_by` needs `gpgv` (Debian and Ubuntu: `apt-get install gpgv`). input-lock never falls back to trusting HTTPS. |
| `the keyring <url> does not match its sha256 in the lock` | The vendor changed its key file. Read the new key, and if you trust it, put its checksum in the lock in the same change. |
| `the signature does not verify: ...` | gpgv rejected the signature: a key not in the pinned keyring, an expired or revoked key, or text that was changed. The message ends with what gpgv said. |
| `this lock is schema 2, and this release ... reads schema 1 only: upgrade input-lock to read it` | The lock uses signed checksums, which input-lock 0.1.0 cannot read. Use 0.2.0 or newer. |
| `this lock is schema 3, and this release ... reads schema 2 only: upgrade input-lock to read it` | The lock pins Debian snapshots per archive (`debian_snapshots`). Use 0.3.0 or newer. |
| `lock: <entry> (<url>) is not the file the lock pins` (from `lock_fetch`) | The download's checksum differs from the lock. The build stops, and the bad file is removed. |
| `lock: LOCK_JQ_URL is not set (is build.env sourced...)` | Source `build.env` before `lock.sh`, and make sure the entry's `used_by` lists the image you passed to `env --image`. |

`input-lock check` prints one line per problem in the lock itself, each after the file's name. Most then start
with `inputs.<entry>`; a problem with the lock as a whole (an unknown top-level field, a bad
`debian_snapshot` or `debian_snapshots`, the schema) names that field instead.
[docs/format.md](docs/format.md) has the rule behind each.

## Status

Version 0.3.0. It adds `debian_snapshots`, one snapshot.debian.org ID per archive, for builds that
take Debian's security archive on its own clock. Only a lock that uses it becomes schema 3; every
other lock keeps its schema and its bytes. Every schema stays readable by every later release, and
a lock from a newer schema fails with a clear message instead of a guess. An older release that
meets a schema 3 lock says to upgrade.

Version 0.2.0 added signed checksums (`signed_by`, `sums_url`, `sums_sig_url`), which make a lock
schema 2, `env --no-snapshot` and `build_env(..., snapshot=False)`, and made `SNAPSHOT_ID`,
`entry_problems`, `entry_env`, `TRUST` and `schema_needed` public.

The library and commands may still change before 1.0, and the release notes on GitHub will say how.

Known limits:

- **Without `signed_by`, the checksum is trust on first use.** For an entry that is not an apt
  one, the checksum is the hash of whatever its URL serves when the version is bumped, so review
  the checksum commit as you would any new dependency (the release, its notes, its signature if it
  has one), not as a proof. An apt entry's checksum comes from its vendor's `Packages` index, read
  over HTTPS and not checked against the vendor's key. Give the entry a `signed_by` where the
  vendor signs (above).
- One entry pins one file for one architecture (several architectures per entry would be schema 4).
- Every push runs the tests on the newest stable Python and checks types and lint against 3.13,
  the oldest supported (Debian 13's); every supported version runs weekly and before each release.

## Questions and bug reports

Open an issue at https://github.com/foreverworks/input-lock/issues. Say the version
(`input-lock --version`), the command you ran, the message you got, and, for a failed `refresh` or
`verify`, the entry's lock text with any private URL taken out. For a security problem, please use
the repository's private vulnerability reporting (Security tab) instead of a public issue.

## License

MIT. See [LICENSE](LICENSE).
