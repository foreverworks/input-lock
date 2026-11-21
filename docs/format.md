# The lock format, schema 1

This is the reference for `lock.json`: every field, every rule `input-lock check` enforces, what a
build reads from it, and how the input identity is computed. The README has the tour.

## The file

JSON, written by tools (Renovate, `input-lock refresh`) and read by people in review. Its own form
is sorted keys, two-space indent, UTF-8 and a trailing newline, so a diff shows only what moved.
`input-lock format` writes that form; `input-lock format --check` fails when a file is not in it.

```json
{
  "schema": 1,
  "inputs": {
    "jq": {
      "datasource": "github-releases",
      "depName": "jqlang/jq",
      "extractVersion": "^jq-(?<version>.+)$",
      "sha256": "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f",
      "url": "https://github.com/jqlang/jq/releases/download/jq-{version}/jq-linux-amd64",
      "used_by": ["builder", "runner"],
      "version": "1.8.2"
    }
  }
}
```

## Top level

| Field | Required | Meaning |
|---|---|---|
| `schema` | no | The format's version, `1`. A lock without it is read as schema 1, so an existing lock needs no edit. A lock with a higher number fails with "upgrade input-lock to read it", never with a guess. |
| `debian_snapshot` | no | A [snapshot.debian.org](https://snapshot.debian.org) ID (`YYYYMMDDTHHMMSSZ`) for builds that install Debian packages from a fixed point in time. Exported as `LOCK_DEBIAN_SNAPSHOT`. |
| `inputs` | yes | One entry per downloaded file, keyed by its name: lowercase letters, digits and dashes. |

Any other top-level field is an error, so a typo never passes silently, and so is a key that
appears twice in one object (JSON would keep the last one and drop an entry without a word).

## An entry

| Field | Required | Meaning |
|---|---|---|
| `version` | yes | The pin. Renovate or a person moves it; a build never picks one. |
| `url` | yes | The download, `https://` only, as a template (below). When Renovate moves the version, the URL must be built from it, so the two can never disagree. |
| `sha256`, `sha512`, `sha1` | at least one | Lowercase hex checksums of that download. `refresh` adds a `sha256` to any entry it touches that lacks one (some vendors publish only sha1). |
| `datasource`, `depName` | together, or neither | Renovate's lookup: where new versions come from. An entry without a datasource is not Renovate's and is moved by hand. |
| `packageName`, `extractVersion`, `versioning`, `registryUrl` | no | The rest of Renovate's lookup, passed to it as they are. |
| `apt` | no | For a `.deb` from a vendor's apt repository: its package name. Requires `datasource: deb`, an `https://` `registryUrl` naming `suite` and `components`, a `sha256`, and a `url` ending in `.deb`. One entry per package name. |
| `module` | no | A name for what installs this input in your build, or `null`. Any name: your own checks decide which are valid. |
| `used_by` | no | A sorted list of distinct names: the images, jobs or machines that install this input. `check --image <name>` (repeated) restricts the names. |
| `description` | no | A note for people. JSON has no comments; this is where one goes. |
| anything else | no | Your own field: a lowercase name (letters, digits, `_`) and a string value. A build may read it, a URL may name it, and it counts in the identity. |

A field named after a derived placeholder (`major`, `version_noepoch`, ...) is refused, as is any
unknown field that is not lowercase (so `buildId`, a likely typo of a Renovate hint, fails).

### URL templates

`{name}` in `url` is replaced by:

| Placeholder | Value for `"version": "5:29.1.0+dfsg-1"` |
|---|---|
| `{version}` | `5:29.1.0+dfsg-1` |
| `{version_noepoch}` | `29.1.0+dfsg-1` (a Debian epoch dropped) |
| `{version_underscore}` | `5:29.1.0_dfsg-1` (`+` as `_`) |
| `{version_urlquoted}` | `5%3A29.1.0%2Bdfsg-1` |
| `{major}` | `29` |
| `{<field>}` | any string field of the entry, your own included (`{build}`) |

A placeholder the entry does not have is an error. `input-lock get <entry>` prints the rendered URL.

### A `.deb` from a vendor's repository

```json
"alloy": {
  "apt": "alloy",
  "datasource": "deb",
  "depName": "alloy",
  "registryUrl": "https://apt.grafana.com?suite=stable&components=main&binaryArch=amd64",
  "sha256": "29ca37327c02e30ff7cc3a1e0ff3779217f26092a1adc29ff4738f6a9e4370e3",
  "url": "https://apt.grafana.com/pool/main/a/alloy/alloy-{version}.amd64.deb",
  "version": "1.22.0-1"
}
```

Renovate's `deb` datasource finds new versions in the repository `registryUrl` names. On a bump,
`refresh` reads that repository's `Packages` index (`dists/<suite>/<component>/binary-<arch>/`,
plain, `.gz` or `.xz`) and takes the checksum it publishes for the new version, without
downloading the package; it checks that the index's file name is the one the URL downloads. An
index that does not list the version fails the run. The index is read over HTTPS and is not
signature-checked: the vendor's TLS is the trust here.

## Rules between entries

- One entry per `apt` package name.
- No two fields may share a build.env name: `foo` with a field `bar_url` and `foo-bar` with a `url`
  would both be `LOCK_FOO_BAR_URL`, and that is refused.

## What a build reads: `build_fields` and build.env

The fields a build reads are every field except Renovate's lookup (`datasource`, `depName`,
`packageName`, `extractVersion`, `versioning`, `registryUrl`) and the bookkeeping (`used_by`,
`description`), with `url` rendered. `input-lock env [--image <name>]` exports exactly those, as
shell-quoted `KEY=value` lines:

```sh
LOCK_ENTRIES='jq shellcheck'
LOCK_JQ_SHA256=b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f
LOCK_JQ_URL=https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64
LOCK_JQ_VERSION=1.8.2
...
```

`LOCK_<ENTRY>` is the name upper-cased with dashes as underscores; `<FIELD>` is the field
upper-cased. `LOCK_DEBIAN_SNAPSHOT` comes first when the lock has one. `input-lock sh` prints
`lock.sh`, the helpers a build sources after build.env:

| Helper | Does |
|---|---|
| `lock_var <entry> <field>` | prints the value, or fails naming the missing key |
| `lock_fetch <entry> <file>` | downloads the URL and checks every checksum the entry has |
| `lock_debs <entry>...` | downloads those `.deb`s, installs them with `apt-get`, and checks each package is at the locked version |

## The input identity

```text
input_identity(packages, entries, recipe_digest, parent_identity) = sha256(canonical_json({
  "identity": 2,
  "packages": sorted set of [name, version, architecture],
  "lock":     {name: build_fields(entry)} for the entries this build installs itself,
  "recipe":   sha256 hex of the build's own recipe, or null,
  "parent":   the identity of what it builds on, or null
}))
```

Canonical JSON is sorted keys, no whitespace, UTF-8. `input-lock identity [--image <name>]`
prints it for lock entries alone (no packages, recipe or parent): a cache key that moves exactly
when a download a job uses moves. It leaves `debian_snapshot` out, as the full identity does (a
new snapshot that changes no installed package rebuilds nothing); a job that installs from the
snapshot without listing its packages should add the snapshot to its cache key itself.

**Which fields count.** `build_fields`, the same projection build.env exports:

| Moves the identity | Leaves it alone |
|---|---|
| `version`, the rendered `url`, every checksum, `apt`, `module`, and every field of your own | `datasource`, `depName`, `packageName`, `extractVersion`, `versioning`, `registryUrl`, `used_by`, `description` |

It is a deny-list on purpose. The fields that stay out are known not to change the bytes a build
makes: how Renovate looks for the next version, which other images use the entry, a note. Every
other field might, including one this library has never heard of, so it counts. An allow-list
would silently ignore a field you add and reuse a stale build; this fails toward a rebuild. The
URL counts rendered, so rewriting a literal URL as a template that renders the same is not a
change.

**Tag 2.** The `"identity": 2` tag names this projection. A digest of whole entries (tag 1, what
earlier in-tree versions of this lock computed) can never compare equal to one of these by
accident; moving from one to the other rebuilds everything once.

`files_digest({path: bytes})` is an order-free digest of a set of files, for a recipe digest;
`lock_digest(lock)` is the sha256 of the lock file's bytes in its own form.

## Schema changes

A change that an older reader would misread (a new required field, a field whose meaning moves)
raises the schema number. A new optional field that older readers can safely refuse does not.
The next known candidate is several architectures per entry (an `{arch}` placeholder and
checksums per architecture), which would be schema 2.

## The library

Everything above is a function in `input_lock`, typed and standard library only:

| Function | Does |
|---|---|
| `parse(text, images=None) -> Lock` | load and check; raises `LockError` naming every problem |
| `problems(data, images=None) -> list[str]` | every problem as a sentence, for your own checks to extend |
| `dump(lock) -> str` | the file's own form |
| `render_url(entry)`, `placeholders(entry)`, `checksums(entry)`, `used_by(entry)` | what an entry says |
| `build_fields(entry)`, `entries_for(lock, image)`, `build_env(lock, image)`, `shell(pairs)` | what a build reads |
| `input_identity(...)`, `files_digest(files)`, `lock_digest(lock)`, `digest(value)`, `canonical(value)` | identities and digests |
| `input_lock.records` | in-toto statements, SLSA provenance v1, SPDX 2.3, test results, DSSE envelopes |
| `input_lock.deb`, `input_lock.fetch`, `input_lock.refresh` | a vendor's index, the network, and the refresh plan |
