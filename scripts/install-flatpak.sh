#!/usr/bin/env bash
set -euo pipefail

# 1.18.4 still has the prune hash-collision bug; 1.19.2 includes upstream
# e12088103aeb8f2b3c45d62b000e2a4051d6a1de. Use the same release in CI and orbs.
version=1.19.2
sha256=161907a8c132fcdf2ef47e612fee79dc70d232b88a4f4ba061966a4769906667
if command -v flatpak >/dev/null && [[ $(flatpak --version) == "Flatpak $version" ]]; then
    echo "Flatpak $version already installed"
    exit 0
fi

SECONDS=0
echo "Building Flatpak $version with the upstream pruning fix"
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl git xz-utils build-essential meson ninja-build pkg-config gettext python3-pyparsing \
    bison libcap-dev libarchive-dev libglib2.0-dev libcurl4-openssl-dev libxml2-dev \
    libzstd-dev libfuse3-dev libostree-dev libjson-glib-dev libappstream-dev \
    libgdk-pixbuf-2.0-dev libseccomp-dev libgpgme-dev libxau-dev
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
curl --fail --location --retry 3 \
    "https://github.com/flatpak/flatpak/releases/download/$version/flatpak-$version.tar.xz" \
    --output "$scratch/flatpak.tar.xz"
echo "$sha256  $scratch/flatpak.tar.xz" | sha256sum --check
tar -xf "$scratch/flatpak.tar.xz" -C "$scratch"
# Packaging and per-user builds need no privileged host service. The release's
# Meson wraps pin the bundled bubblewrap and D-Bus proxy to exact Git commits.
# Use Debian/Ubuntu's Python with the apt-installed build modules, not orb Python.
env PATH=/usr/bin:/bin meson setup "$scratch/build" "$scratch/flatpak-$version" \
    --prefix=/usr/local --buildtype=release \
    -Dsystem_helper=disabled -Dsystemd=disabled -Dselinux_module=disabled \
    -Dmalcontent=disabled -Ddconf=disabled -Dwayland_security_context=disabled \
    -Dgir=disabled -Dgtkdoc=disabled -Ddocbook_docs=disabled -Dman=disabled -Dtests=false
env PATH=/usr/bin:/bin meson compile -C "$scratch/build" -j 2
sudo env PATH=/usr/bin:/bin meson install -C "$scratch/build"
sudo ldconfig
hash -r
test "$(flatpak --version)" = "Flatpak $version"
echo "Flatpak $version ready (${SECONDS}s elapsed)"
