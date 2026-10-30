import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts import upstream

CLAUDE = upstream.APPS["io.github.rosenk.ClaudeDesktop"]
GROK = upstream.APPS["io.github.rosenk.GrokBot"]


def package(version, arch="amd64", name="claude-desktop", filename=None):
    filename = filename or f"pool/main/c/claude-desktop/claude-desktop_{version}_{arch}.deb"
    return f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\nFilename: {filename}\nSHA256: {'a' * 64}\nDescription: test\n continuation\n"


def run_main(directory, discovered, published, force=False):
    args = ["upstream", "--output", directory, "--published-url", "https://example.com/versions.json"]
    if force:
        args.append("--force")
    fetch = {"side_effect": published} if isinstance(published, Exception) else {"return_value": json.dumps(published).encode()}
    with patch.object(upstream, "discover", side_effect=lambda app: discovered[app["package"]]), patch.object(upstream, "fetch", **fetch), patch("sys.argv", args), patch.dict("os.environ", {"GITHUB_OUTPUT": str(Path(directory) / "output")}):
        upstream.main()
    return (Path(directory) / "output").read_text()


class UpstreamTests(unittest.TestCase):
    def test_debian_order_not_lexical_or_index_order(self):
        data = "\n".join([package("2.10.0"), package("99.0", "arm64"), package("88.0", name="other"), package("2.9.0")])
        self.assertEqual(upstream.latest_package(data, "amd64", CLAUDE)["version"], "2.10.0")
        self.assertEqual(upstream.latest_package(data, "arm64", CLAUDE)["version"], "99.0")
        self.assertGreater(upstream.compare_versions("1:1.0-1", "9.0-2"), 0)
        self.assertLess(upstream.compare_versions("2.0~beta1", "2.0"), 0)

    def test_missing_architecture_fails(self):
        with self.assertRaises(ValueError):
            upstream.latest_package(package("1.0"), "arm64", CLAUDE)

    def test_packages_are_matched_to_their_own_app_and_pool(self):
        grok = package("0.78.1", name="grok-bot", filename="pool/grok-bot/g/gr/grok-bot_0.78.1_amd64.deb")
        data = "\n".join([package("9.0"), grok])
        result = upstream.latest_package(data, "amd64", GROK)
        self.assertEqual(result["url"], "https://downloads.cursor.com/aptrepo/pool/grok-bot/g/gr/grok-bot_0.78.1_amd64.deb")
        # A Grok Bot entry outside its pool is rejected even though the name matches.
        with self.assertRaises(ValueError):
            upstream.latest_package(package("1.0", name="grok-bot"), "amd64", GROK)

    def test_signing_key_must_be_the_single_pinned_primary_key(self):
        for app in (CLAUDE, GROK):
            pinned = f"pub:::::::::\nfpr:::::::::{app['fingerprint']}:\n"
            other = GROK if app is CLAUDE else CLAUDE
            for keys in (f"pub:::::::::\nfpr:::::::::{other['fingerprint']}:\n", pinned + "pub:::::::::\nfpr:::::::::EXTRA:\n"):
                with self.subTest(app=app["package"], keys=keys), patch.object(upstream, "fetch", return_value=b"key"), patch.object(upstream.subprocess, "check_output", return_value=keys), patch.object(upstream.subprocess, "run") as run:
                    with self.assertRaises(ValueError):
                        upstream.discover(app)
                    run.assert_not_called()

    def test_unexpected_package_paths_fail(self):
        for path in ("pool/main/c/claude-desktop/../../evil.deb", "/pool/main/c/claude-desktop/test.deb", "https://evil.example/test.deb"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                upstream.latest_package(package("1.0", filename=path), "amd64", CLAUDE)

    def test_index_hash_and_size_are_both_verified(self):
        data = b"test index"
        digest = hashlib.sha256(data).hexdigest()
        release = f"SHA256:\n {digest} {len(data)} main/binary-amd64/Packages\nSHA512:\n ignored\n"
        upstream.verify_index(release, "main/binary-amd64/Packages", data)
        for bad in (b"test indeX", data + b"x"):
            with self.assertRaises(ValueError):
                upstream.verify_index(release, "main/binary-amd64/Packages", bad)
        with self.assertRaises(ValueError):
            upstream.verify_index(release.replace("SHA256:", "SHA1:"), "main/binary-amd64/Packages", data)

    def test_manifests_pin_app_and_arch_specific_urls_and_hashes(self):
        packages = {
            "io.github.rosenk.ClaudeDesktop": {
                "x86_64": {"version": "2.0", "url": "https://example.com/x.deb", "sha256": "a" * 64},
                "aarch64": {"version": "1.9", "url": "https://example.com/a.deb", "sha256": "b" * 64},
            },
            "io.github.rosenk.GrokBot": {
                "x86_64": {"version": "0.78.1", "url": "https://example.com/gx.deb", "sha256": "c" * 64},
                "aarch64": {"version": "0.78.0", "url": "https://example.com/ga.deb", "sha256": "d" * 64},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            upstream.generate(packages, output)
            for app_id, arches in packages.items():
                for arch, expected in arches.items():
                    manifest = json.loads((output / f"{app_id}-{arch}.json").read_text())
                    self.assertEqual(manifest["app-id"], app_id)
                    source, *local = manifest["modules"][0]["sources"]
                    self.assertEqual((source["url"], source["sha256"]), (expected["url"], expected["sha256"]))
                    self.assertTrue(local)
                    for item in local:
                        self.assertTrue((output / item["path"]).is_file(), item["path"])
            self.assertEqual(json.loads((output / "versions.json").read_text()), packages)

    def test_rebuild_decision_uses_deployed_checksums_and_force(self):
        claude = {"x86_64": {"version": "1.0", "url": "https://example.com/x.deb", "sha256": "a" * 64}}
        grok = {"x86_64": {"version": "0.1", "url": "https://example.com/g.deb", "sha256": "c" * 64}}
        discovered = {"claude-desktop": claude, "grok-bot": grok}
        current = {"io.github.rosenk.ClaudeDesktop": claude, "io.github.rosenk.GrokBot": grok}
        for published, force, expected in (
            (current, False, "false"),
            (current, True, "true"),
            # The live site before this change only lists Claude, keyed by arch.
            (claude, False, "true"),
            ({**current, "io.github.rosenk.GrokBot": {"x86_64": {**grok["x86_64"], "sha256": "b" * 64}}}, False, "true"),
        ):
            with self.subTest(force=force, published=published), tempfile.TemporaryDirectory() as directory:
                self.assertEqual(run_main(directory, discovered, published, force), f"changed={expected}\n")

    def test_downgrade_is_refused_even_when_forced(self):
        grok = {"x86_64": {"version": "0.9", "url": "https://example.com/g.deb", "sha256": "c" * 64}}
        published = {"io.github.rosenk.GrokBot": {"x86_64": {**grok["x86_64"], "version": "0.10"}}}
        for force in (False, True):
            with self.subTest(force=force), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    run_main(directory, {"claude-desktop": {}, "grok-bot": grok}, published, force)
                self.assertFalse((Path(directory) / "output").exists())

    def test_first_deploy_404_but_server_errors_fail(self):
        for code in (404, 503):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                error = urllib.error.HTTPError("https://example.com/versions.json", code, "test", {}, None)
                if code == 404:
                    self.assertEqual(run_main(directory, {"claude-desktop": {}, "grok-bot": {}}, error), "changed=true\n")
                else:
                    with self.assertRaises(urllib.error.HTTPError):
                        run_main(directory, {"claude-desktop": {}, "grok-bot": {}}, error)
                    self.assertFalse((Path(directory) / "output").exists())


if __name__ == "__main__":
    unittest.main()
