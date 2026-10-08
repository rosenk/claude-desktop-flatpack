#!/usr/bin/env bash
set -euo pipefail

# This script only prepares local output; the workflow performs the deployment.
: "${GNUPGHOME:?Use an isolated GPG home with the repository signing key imported}"
: "${FLATPAK_GPG_KEY_ID:?Set the signing key fingerprint}"
: "${REPO_URL:?Set the public HTTPS URL ending in /repo}"
mkdir -p dist/repo
ostree --repo=dist/repo init --mode=archive-z2
for arch in x86_64 aarch64; do
    ref="app/io.github.rosenk.ClaudeDesktop/$arch/stable"
    ostree --repo=dist/repo pull-local "repos/$arch" "$ref"
    ostree --repo=dist/repo gpg-sign --gpg-homedir="$GNUPGHOME" "$ref" "$FLATPAK_GPG_KEY_ID"
done
flatpak build-update-repo --prune --prune-depth=0 \
    --gpg-sign="$FLATPAK_GPG_KEY_ID" --gpg-homedir="$GNUPGHOME" \
    --title="Claude Desktop — unofficial Flatpak" dist/repo
gpg --batch --export "$FLATPAK_GPG_KEY_ID" > dist/repo-key.gpg
test -s dist/repo-key.gpg
cat > dist/claude-desktop.flatpakrepo <<EOF
[Flatpak Repo]
Title=Claude Desktop (unofficial)
Url=$REPO_URL
Homepage=https://github.com/rosenk/claude-desktop-flatpack
Comment=Unofficial repackaging of Anthropic's official Linux app
Description=Claude Desktop for x86_64 and ARM64. Sandbox limitations apply.
DefaultBranch=stable
GPGKey=$(base64 -w0 dist/repo-key.gpg)
EOF
cp generated/versions.json dist/versions.json
# Keep the repository below GitHub Pages' 1 GB published-site limit.
bytes=$(du -sb dist | cut -f1)
if (( bytes >= 950000000 )); then
    echo "Repository is too large for GitHub Pages ($bytes bytes)" >&2
    exit 1
fi
ostree --repo=dist/repo fsck
