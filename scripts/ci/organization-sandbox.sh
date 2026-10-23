#!/usr/bin/env bash
# Ubuntu 22.04 only: upstream release verified before installation. No sysctl changes.
set -euo pipefail
# Labels are fixed call-site constants; never log command arguments or environment.
run_stage() {
  local stage="$1" status
  shift
  printf 'organization-sandbox stage=%s event=start\n' "$stage" >&2
  if "$@"; then
    printf 'organization-sandbox stage=%s event=end\n' "$stage" >&2
  else
    status=$?
    printf 'organization-sandbox stage=%s event=failed\n' "$stage" >&2
    return "$status"
  fi
}

source /etc/os-release
[[ "$ID" = ubuntu && "$VERSION_ID" = 22.04 ]] || { echo "Expected Ubuntu 22.04" >&2; exit 1; }
run_stage package_index sudo apt-get update -qq
run_stage build_dependencies sudo apt-get install -y build-essential meson ninja-build pkg-config libcap-dev libseccomp2 libselinux1-dev
build_root="$(run_stage temporary_directory mktemp -d)"
trap 'run_stage cleanup rm -rf "$build_root"' EXIT
run_stage release_download curl --fail --location --retry 2 https://github.com/containers/bubblewrap/releases/download/v0.13.0/bubblewrap-0.13.0.tar.xz -o "$build_root/release.tar.xz"
echo '4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765  '"$build_root/release.tar.xz" | run_stage release_checksum sha256sum -c -
run_stage release_extract tar -xJf "$build_root/release.tar.xz" -C "$build_root"
run_stage build_configure meson setup "$build_root/build" "$build_root/bubblewrap-0.13.0" --prefix=/usr -Dman=disabled -Dbash_completion=disabled -Dzsh_completion=disabled -Dtests=false
run_stage build_compile meson compile -C "$build_root/build"
run_stage build_install sudo meson install -C "$build_root/build"
run_stage installed_version /usr/bin/bwrap --version
