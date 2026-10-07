#!/usr/bin/env bash
set -euo pipefail
# Test-only compatibility setup: Ubuntu 22.04's packaged bubblewrap predates
# required --disable-userns. Install the verified official release normally;
# never change kernel, AppArmor, network or namespace permission settings.
sudo apt-get update -qq
sudo apt-get install -y build-essential meson ninja-build pkg-config libcap-dev libseccomp2 libselinux1-dev
build_root="$(mktemp -d)"
curl --fail --location --retry 2 https://github.com/containers/bubblewrap/releases/download/v0.13.0/bubblewrap-0.13.0.tar.xz -o "$build_root/release.tar.xz"
echo '4734237473c0e5d695e4e9034a34e43b2dbf5164655bd13fa59ae376b2b7a765  '"$build_root/release.tar.xz" | sha256sum -c -
tar -xJf "$build_root/release.tar.xz" -C "$build_root"
meson setup "$build_root/build" "$build_root/bubblewrap-0.13.0" --prefix=/usr -Dman=disabled -Dbash_completion=disabled -Dzsh_completion=disabled -Dtests=false
meson compile -C "$build_root/build"
sudo meson install -C "$build_root/build"
/usr/bin/bwrap --version
