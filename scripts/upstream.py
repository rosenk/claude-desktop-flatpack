#!/usr/bin/env python3
"""Authenticate the vendors' APT metadata and generate pinned Flatpak manifests."""

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from xml.sax.saxutils import escape

APPS = {
    "io.github.rosenk.ClaudeDesktop": {
        "base": "https://downloads.claude.ai/claude-desktop/apt/stable/",
        "suite": "stable",
        "key_url": "https://downloads.claude.ai/claude-desktop/key.asc",
        "fingerprint": "31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE",
        "package": "claude-desktop",
        "pool": "pool/main/c/claude-desktop/",
    },
    "io.github.rosenk.GrokBot": {
        "base": "https://downloads.cursor.com/aptrepo/",
        "suite": "grok-bot",
        "key_url": "https://downloads.cursor.com/keys/anysphere.asc",
        "fingerprint": "380FF4BCDC34A4BD92A3565342A1772E62E492D6",
        "package": "grok-bot",
        "pool": "pool/grok-bot/g/gr/",
    },
}
ARCHES = {"x86_64": "amd64", "aarch64": "arm64"}
ROOT = Path(__file__).resolve().parents[1]


def fetch(url):
    # Cursor's CDN rejects Python's default User-Agent with 403.
    request = urllib.request.Request(url, headers={"User-Agent": "claude-desktop-flatpack"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def paragraphs(data):
    for paragraph in data.strip().split("\n\n"):
        fields = {}
        for line in paragraph.splitlines():
            if line and not line[0].isspace() and ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        yield fields


def compare_versions(left, right):
    for operator, result in (("gt", 1), ("lt", -1)):
        if subprocess.run(["dpkg", "--compare-versions", left, operator, right]).returncode == 0:
            return result
    return 0


def latest_package(data, arch, app):
    packages = [p for p in paragraphs(data) if p.get("Package") == app["package"] and p.get("Architecture") == arch]
    if not packages:
        raise ValueError(f"No {app['package']} package for {arch}")
    package = max(packages, key=functools.cmp_to_key(lambda a, b: compare_versions(a["Version"], b["Version"])))
    path = PurePosixPath(package["Filename"])
    if path.is_absolute() or ".." in path.parts or not str(path).startswith(app["pool"]):
        raise ValueError("Unexpected package path")
    if not re.fullmatch(r"[0-9a-f]{64}", package["SHA256"]):
        raise ValueError("Invalid package SHA256")
    return {"version": package["Version"], "url": app["base"] + str(path), "sha256": package["SHA256"]}


def verify_index(release, path, data):
    # Only use hashes from the SHA256 section, never SHA1/MD5.
    in_sha256 = False
    for line in release.splitlines():
        if line == "SHA256:":
            in_sha256 = True
        elif line and not line[0].isspace():
            in_sha256 = False
        elif in_sha256:
            digest, size, name = line.split()
            if name == path:
                if len(data) != int(size) or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError(f"Index checksum mismatch: {path}")
                return
    raise ValueError(f"Missing signed SHA256: {path}")


def discover(app):
    with tempfile.TemporaryDirectory() as directory:
        home = Path(directory)
        (home / "key.asc").write_bytes(fetch(app["key_url"]))
        result = subprocess.check_output(["gpg", "--homedir", directory, "--batch", "--with-colons", "--show-keys", str(home / "key.asc")], text=True)
        fingerprints = [line.split(":")[9] for line in result.splitlines() if line.startswith("fpr:")]
        primary_keys = sum(line.startswith("pub:") for line in result.splitlines())
        if primary_keys != 1 or not fingerprints or fingerprints[0] != app["fingerprint"]:
            raise ValueError(f"{app['package']} signing key fingerprint changed; review before trusting it")
        subprocess.run(["gpg", "--homedir", directory, "--batch", "--yes", "--dearmor", "--output", str(home / "key.gpg"), str(home / "key.asc")], check=True)
        dists = f"{app['base']}dists/{app['suite']}/"
        (home / "InRelease").write_bytes(fetch(dists + "InRelease"))
        subprocess.run(["gpgv", "--homedir", directory, "--keyring", str(home / "key.gpg"), "--output", str(home / "Release"), str(home / "InRelease")], check=True)
        release = (home / "Release").read_text()
        fields = next(paragraphs(release))
        now = datetime.now(timezone.utc)
        # Cursor's Release has no Valid-Until; main() refuses downgrades instead.
        valid_until = fields.get("Valid-Until")
        if parsedate_to_datetime(fields["Date"]) > now or (valid_until and now >= parsedate_to_datetime(valid_until)):
            raise ValueError("APT metadata is expired or dated in the future")
        packages = {}
        for flatpak_arch, deb_arch in ARCHES.items():
            path = f"main/binary-{deb_arch}/Packages"
            index = fetch(dists + path)
            verify_index(release, path, index)
            packages[flatpak_arch] = latest_package(index.decode(), deb_arch, app)
        return packages


def generate(packages, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "versions.json").write_text(json.dumps(packages, indent=2) + "\n")
    for app_id, arches in packages.items():
        for arch, package in arches.items():
            manifest = json.loads((ROOT / "packaging" / f"{app_id}.json").read_text())
            module = manifest["modules"][0]
            local = []
            for source in module["sources"]:
                path = ROOT / "packaging" / source["path"]
                if path.name.endswith(".metainfo.xml"):
                    # Software centres show the upstream version from <releases>.
                    release = f'  <releases>\n    <release version="{escape(package["version"], {chr(34): "&quot;"})}" />\n  </releases>\n</component>'
                    path = output / f"{app_id}-{arch}.metainfo.xml"
                    path.write_text((ROOT / "packaging" / source["path"]).read_text().replace("</component>", release))
                    source = {**source, "dest-filename": PurePosixPath(source["path"]).name}
                local.append({**source, "path": os.path.relpath(path, output)})
            module["sources"] = [{"type": "file", "url": package["url"], "sha256": package["sha256"], "dest-filename": "upstream.deb"}] + local
            (output / f"{app_id}-{arch}.json").write_text(json.dumps(manifest, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "generated")
    parser.add_argument("--published-url")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    packages = {app_id: discover(app) for app_id, app in APPS.items()}
    published = None
    if args.published_url:
        try:
            published = json.loads(fetch(args.published_url))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
    for app_id, arches in packages.items():
        for arch, package in arches.items():
            old = (published or {}).get(app_id, {}).get(arch)
            if old and compare_versions(package["version"], old["version"]) < 0:
                raise ValueError(f"Refusing downgrade of {app_id}/{arch} from {old['version']} to {package['version']}")
    generate(packages, args.output)
    changed = args.force or packages != published
    print(json.dumps(packages, indent=2))
    print(f"changed={str(changed).lower()}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"changed={str(changed).lower()}\n")


if __name__ == "__main__":
    main()
