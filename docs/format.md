# The lock format, schema 1

This is the reference for `lock.json`: every field, every rule `input-lock check` enforces, what a
build reads from it, and how the input identity is computed. The README has the tour.

## The file

JSON, written by tools (Renovate, `input-lock refresh`) and read by people in review. Its own form
is sorted keys, two-space indent, UTF-8 and a trailing newline, so a diff shows only what moved.
`input-lock format` writes that form; `input-lock format --check` fails when a file is not in it.
`refresh` (and so the Action) refuses a lock that is not in its form, so the commit that carries new
checksums changes checksums and nothing else.

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
| `signed_by` | no | The name of another entry: the keyring the vendor signs with. Its checksum is then taken from what the vendor signed, never from HTTPS alone (below). |
| `sums_url`, `sums_sig_url` | no | For a `signed_by` entry that is not an apt one: a URL template of the vendor's checksum list, clear-signed, or signed by the detached signature at `sums_sig_url`. Both `https://`. |
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
index that does not list the version, or lists a checksum that is not a hex digest, fails the run.
Without `signed_by` the index is read over HTTPS and is not signature-checked: the vendor's TLS
is the trust. With it, the index counts only when its sha256 is the one the repository's signed
`InRelease` (or `Release` and `Release.gpg`) lists, below.

### Signed checksums: `signed_by`

```json
"alloy": {
  "apt": "alloy",
  ...
  "signed_by": "grafana-apt-key"
},
"grafana-apt-key": {
  "description": "The key Grafana signs its apt repository with: a trust root, moved by hand.",
  "sha256": "<sha256 of gpg.key>",
  "url": "https://apt.grafana.com/gpg.key",
  "version": "1"
}
```

`signed_by` names the entry that pins the vendor's OpenPGP public keys, as apt's own `Signed-By`
does. `refresh` and `verify` download that keyring, check it against its checksums in the lock
(a mismatch fails: a keyring is moved by hand, its checksum with it), unwrap it if it is
ASCII-armored, and hand it to `gpgv`:

| Entry | What is signed | What is checked |
|---|---|---|
| apt (`apt` set) | `<repository>/dists/<suite>/InRelease`, or `Release` with `Release.gpg` | the signature; `Suite` or `Codename` is the `registryUrl`'s suite; `Valid-Until`, when present, is in the future; the `Packages` index read (plain, `.gz` or `.xz`) has the sha256 the signed file lists for it |
| any other | the list at `sums_url`, clear-signed, or with the detached signature at `sums_sig_url` | the signature; exactly one line names the entry's file, `<hex>  <name>` or `<hex> *<name>`, where the name is the url's path relative to the list's folder (or its file name, for a url elsewhere); the hex's length says its algorithm (64 sha256, 128 sha512, 40 sha1) |

A signature passes when `gpgv --status-fd` reports `VALIDSIG` and no `BADSIG`, `EXPSIG`,
`EXPKEYSIG`, `REVKEYSIG` or `ERRSIG`, and `gpgv` exits 0. Only the text `gpgv` writes out as
signed is read. A clear-signed file must be one signed message and nothing else, so text around it
cannot pose as signed. Without `gpgv` on the PATH, a `signed_by` entry fails `refresh` and
`verify` with a message naming it; nothing falls back to HTTPS.

The rules `check` applies, offline:

- `signed_by` names another entry of the lock, which has no `signed_by` and no `apt` itself.
- An entry with `signed_by` that is not an apt one has a `sums_url`; an apt entry has none.
- `sums_url` and `sums_sig_url` need `signed_by` (and `sums_sig_url` a `sums_url`), render from the
  entry's fields as `url` does, and are `https://`.

`signed_by`, `sums_url` and `sums_sig_url` stay out of build.env and the identity: they say how a
checksum was vouched for, and the checksum, which counts, says what the bytes are. input-lock 0.1.0
reads them as fields of your own and checks no signature, so a lock that relies on them needs
0.2.0 or later.

### What a checksum vouches for

For an entry that is not an apt one, `refresh` downloads the file the new version's URL names and
writes the hash of what it received. Nothing is cross-checked against a checksum the upstream
publishes (a `SHA256SUMS` file, a signature, an attestation), so the checksum proves only that a
later download is the same bytes as the one taken at bump time. A reviewer treats the checksum
commit as trust on first use: the bump itself (the release, its notes, its signature if it has
one) is what gets reviewed, and the checksum commit adds nothing to it. An apt entry has one more
source: its sha256 is the one the vendor publishes in its index, not a hash of a download.

An entry with `signed_by` is the exception: its checksum is the one the vendor signed, checked
against a keyring the lock pins, so the checksum commit then vouches for what the vendor
published, as far as the vendor's key does.

## Rules between entries

- One entry per `apt` package name.
- No two fields may share a build.env name: `foo` with a field `bar_url` and `foo-bar` with a `url`
  would both be `LOCK_FOO_BAR_URL`, and that is refused.

## What a build reads: `build_fields` and build.env

The fields a build reads are every field except Renovate's lookup (`datasource`, `depName`,
`packageName`, `extractVersion`, `versioning`, `registryUrl`), the bookkeeping (`used_by`,
`description`) and the trust (`signed_by`, `sums_url`, `sums_sig_url`), with `url` rendered. `input-lock env [--image <name>]` exports exactly those, as
shell-quoted `KEY=value` lines:

```sh
LOCK_ENTRIES='jq shellcheck'
LOCK_JQ_SHA256=b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f
LOCK_JQ_URL=https://github.com/jqlang/jq/releases/download/jq-1.8.2/jq-linux-amd64
LOCK_JQ_VERSION=1.8.2
...
```

`LOCK_<ENTRY>` is the name upper-cased with dashes as underscores; `<FIELD>` is the field
upper-cased. `LOCK_DEBIAN_SNAPSHOT` comes first when the lock has one, unless `env --no-snapshot`
(`build_env(..., snapshot=False)`) leaves it out, for an environment that must not see it. `input-lock sh` prints
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
| `version`, the rendered `url`, every checksum, `apt`, `module`, and every field of your own | `datasource`, `depName`, `packageName`, `extractVersion`, `versioning`, `registryUrl`, `used_by`, `description`, `signed_by`, `sums_url`, `sums_sig_url` |

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
| `entry_problems(name, entry, images=None) -> list[str]` | the same for one entry, alone (no rule between entries) |
| `SNAPSHOT_ID` | the pattern a `debian_snapshot` matches (`SNAPSHOT_ID.fullmatch(text)`) |
| `dump(lock) -> str` | the file's own form |
| `render_url(entry)`, `placeholders(entry)`, `checksums(entry)`, `used_by(entry)` | what an entry says |
| `build_fields(entry)`, `entries_for(lock, image)`, `entry_env(name, entry)`, `shell(pairs)` | what a build reads |
| `build_env(lock, image=None, snapshot=True)` | a build.env's pairs; `snapshot=False` leaves `LOCK_DEBIAN_SNAPSHOT` out |
| `input_identity(...)`, `files_digest(files)`, `lock_digest(lock)`, `digest(value)`, `canonical(value)` | identities and digests |
| `input_lock.records` | in-toto statements, SLSA provenance v1, SPDX 2.3, test results, DSSE envelopes |
| `input_lock.deb`, `input_lock.fetch`, `input_lock.refresh` | a vendor's index, the network, and the refresh plan |
| `fetch.keyring_for(lock, entry)`, `fetch.checksums_for(entry, ring)`, `fetch.verify(name, entry, ring)` | a `signed_by` entry's keyring, then its checksums or its verification, signature checked |
| `input_lock.signed` | gpgv's verdict, one clear-signed message, armor, a checksum list: the pure half of the signature check |
