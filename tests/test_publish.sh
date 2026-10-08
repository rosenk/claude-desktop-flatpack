#!/usr/bin/env bash
# End-to-end signing/merge test using tiny synthetic repos, not Claude binaries.
set -euo pipefail
root=$(pwd)
scratch=$(mktemp -d)
export GNUPGHOME="$scratch/gnupg"
export XDG_DATA_HOME="$scratch/data"
cleanup() {
    gpgconf --kill all
    rm -rf "$scratch"
}
trap cleanup EXIT
mkdir -m700 "$GNUPGHOME"
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key 'Disposable packaging test <test@example.invalid>' rsa2048 sign 0
FLATPAK_GPG_KEY_ID=$(gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" {print $10; exit}')
export FLATPAK_GPG_KEY_ID
export REPO_URL="file://$scratch/dist/repo"
mkdir -p "$scratch/generated"
printf '{}\n' > "$scratch/generated/versions.json"
for arch in x86_64 aarch64; do
    tree="$scratch/tree-$arch"
    mkdir -p "$tree/files/bin" "$scratch/repos/$arch"
    printf '%s\n' "$arch" > "$tree/files/bin/fixture"
    cat > "$tree/metadata" <<EOF
[Application]
name=io.github.rosenk.ClaudeDesktop
runtime=org.freedesktop.Platform/$arch/25.08
sdk=org.freedesktop.Sdk/$arch/25.08
EOF
    ostree --repo="$scratch/repos/$arch" init --mode=archive-z2
    ostree --repo="$scratch/repos/$arch" commit \
        --branch="app/io.github.rosenk.ClaudeDesktop/$arch/stable" --tree="dir=$tree"
done
# Run in a disposable output directory, not the working repository.
(cd "$scratch" && bash "$root/scripts/publish.sh")
flatpak remote-add --user fixture "$scratch/dist/claude-desktop.flatpakrepo"
ostree --repo="$scratch/dist/repo" remote add --gpg-import="$scratch/dist/repo-key.gpg" fixture "$REPO_URL"
for arch in x86_64 aarch64; do
    flatpak remote-info --user --arch="$arch" fixture io.github.rosenk.ClaudeDesktop
    ref="app/io.github.rosenk.ClaudeDesktop/$arch/stable"
    test "$(ostree --repo="$scratch/dist/repo" cat "$ref" /files/bin/fixture)" = "$arch"
    ostree --repo="$scratch/dist/repo" show --gpg-verify-remote=fixture "$ref"
done
# A signature with the wrong public key must be refused.
gpg --batch --pinentry-mode loopback --passphrase '' \
    --quick-generate-key 'Wrong test key <wrong@example.invalid>' rsa2048 sign 0
wrong=$(gpg --batch --with-colons --list-secret-keys wrong@example.invalid | awk -F: '$1 == "fpr" {print $10; exit}')
gpg --batch --export "$wrong" > "$scratch/wrong.gpg"
if flatpak remote-add --user --gpg-import="$scratch/wrong.gpg" wrong-key "$REPO_URL" &&
    flatpak remote-ls --user wrong-key; then
    echo 'ERROR: repository accepted with wrong signing key' >&2
    exit 1
fi
echo 'PASS: both architectures merged, signed and verified; wrong key rejected'
