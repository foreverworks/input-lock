# shellcheck shell=bash
# lock.sh: the shell helpers a build sources to install what the lock pins. Source it after the
# build.env that `input-lock env` wrote (LOCK_<ENTRY>_<FIELD>=value lines). Nothing here resolves
# a version: every download is the lock's URL, checked against the lock's checksums, and a vendor's
# package goes in as the very .deb the lock names, never as "the repository's newest".
#
#   . ./build.env && . ./lock.sh            (input-lock env > build.env; input-lock sh > lock.sh)
#
#   lock_var <entry> <field>     print LOCK_<ENTRY>_<FIELD>, or fail naming it
#   lock_fetch <entry> <file>    download the entry's url to <file> and verify every checksum it has
#   lock_debs <entry>...         install those apt entries' .debs (dependencies from the apt sources
#                                already configured), then check each package is at the lock's version
#
# Needs bash, curl and coreutils (sha256sum, sha512sum, sha1sum); lock_debs needs apt-get and dpkg.

lock_key() {
  printf 'LOCK_%s_%s' "$(tr 'a-z-' 'A-Z_' <<<"$1")" "$(tr 'a-z-' 'A-Z_' <<<"$2")"
}

lock_var() {
  local key
  key=$(lock_key "$1" "$2")
  [ -n "${!key:-}" ] || { echo "lock: ${key} is not set (is build.env sourced, and does $1 list this build in used_by?)" >&2; return 1; }
  printf '%s\n' "${!key}"
}

lock_fetch() {
  local entry=$1 file=$2 url algorithm key sum checked=0
  url=$(lock_var "$entry" url) || return 1
  curl -fsSL --retry 3 --connect-timeout 15 --max-time 3600 "$url" -o "$file" || { echo "lock: ${entry}: could not download ${url}" >&2; return 1; }
  for algorithm in sha256 sha512 sha1; do
    key=$(lock_key "$entry" "$algorithm")
    sum=${!key:-}
    [ -n "$sum" ] || continue
    echo "${sum}  ${file}" | "${algorithm}sum" -c --quiet - || { echo "lock: ${entry} (${url}) is not the file the lock pins" >&2; return 1; }
    checked=1
  done
  [ "$checked" = 1 ] || { echo "lock: ${entry} has no checksum in build.env" >&2; return 1; }
}

lock_debs() {
  local dir entry files=() package version have
  dir=$(mktemp -d "${TMPDIR:-/tmp}/lock-debs.XXXXXX")
  for entry in "$@"; do
    package=$(lock_var "$entry" apt) || return 1
    lock_fetch "$entry" "${dir}/${package}.deb" || return 1
    files+=("${dir}/${package}.deb")
  done
  apt-get -y install --no-install-recommends "${files[@]}" || return 1
  for entry in "$@"; do
    package=$(lock_var "$entry" apt) && version=$(lock_var "$entry" version) || return 1
    have=$(dpkg-query -W -f='${Version}' "$package")
    [ "$have" = "$version" ] || { echo "lock: ${package} is ${have}, the lock pins ${version}" >&2; return 1; }
  done
  rm -rf "$dir"
}
