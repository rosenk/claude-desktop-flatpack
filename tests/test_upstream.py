import hashlib
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts import upstream


def package(version, arch="amd64", name="claude-desktop", filename=None):
    filename = filename or f"pool/main/c/claude-desktop/claude-desktop_{version}_{arch}.deb"
    return f"Package: {name}\nVersion: {version}\nArchitecture: {arch}\nFilename: {filename}\nSHA256: {'a' * 64}\nDescription: test\n continuation\n"


class UpstreamTests(unittest.TestCase):
    def test_debian_order_not_lexical_or_index_order(self):
        data = "\n".join([package("2.10.0"), package("99.0", "arm64"), package("88.0", name="other"), package("2.9.0")])
        self.assertEqual(upstream.latest_package(data, "amd64")["version"], "2.10.0")
        self.assertEqual(upstream.latest_package(data, "arm64")["version"], "99.0")
        self.assertGreater(upstream.compare_versions("1:1.0-1", "9.0-2"), 0)
        self.assertLess(upstream.compare_versions("2.0~beta1", "2.0"), 0)

    def test_missing_architecture_fails(self):
        with self.assertRaises(ValueError):
            upstream.latest_package(package("1.0"), "arm64")

    def test_signing_key_must_be_the_single_pinned_primary_key(self):
        pinned = f"pub:::::::::\nfpr:::::::::{upstream.FINGERPRINT}:\n"
        for keys in ("pub:::::::::\nfpr:::::::::WRONG:\n", pinned + "pub:::::::::\nfpr:::::::::EXTRA:\n"):
            with self.subTest(keys=keys), patch.object(upstream, "fetch", return_value=b"key"), patch.object(upstream.subprocess, "check_output", return_value=keys), patch.object(upstream.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    upstream.discover()
                run.assert_not_called()

    def test_unexpected_package_paths_fail(self):
        for path in ("pool/main/c/claude-desktop/../../evil.deb", "/pool/main/c/claude-desktop/test.deb", "https://evil.example/test.deb"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                upstream.latest_package(package("1.0", filename=path), "amd64")

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

    def test_manifests_pin_arch_specific_urls_and_hashes(self):
        packages = {
            "x86_64": {"version": "2.0", "url": "https://example.com/x.deb", "sha256": "a" * 64},
            "aarch64": {"version": "1.9", "url": "https://example.com/a.deb", "sha256": "b" * 64},
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            upstream.generate(packages, output)
            for arch in packages:
                manifest = json.loads((output / f"{arch}.json").read_text())
                source = manifest["modules"][0]["sources"][0]
                self.assertEqual(source["url"], packages[arch]["url"])
                self.assertEqual(source["sha256"], packages[arch]["sha256"])
                self.assertTrue((output / manifest["modules"][0]["sources"][1]["path"]).is_file())
            self.assertEqual(json.loads((output / "versions.json").read_text()), packages)

    def test_rebuild_decision_uses_deployed_checksums_and_force(self):
        packages = {"x86_64": {"version": "1.0", "url": "https://example.com/x.deb", "sha256": "a" * 64}}
        for published, force, expected in (
            (packages, False, "false"),
            (packages, True, "true"),
            ({"x86_64": {**packages["x86_64"], "sha256": "b" * 64}}, False, "true"),
        ):
            with self.subTest(force=force, published=published), tempfile.TemporaryDirectory() as directory:
                args = ["upstream", "--output", directory, "--published-url", "https://example.com/versions.json"]
                if force:
                    args.append("--force")
                with patch.object(upstream, "discover", return_value=packages), patch.object(upstream, "fetch", return_value=json.dumps(published).encode()), patch("sys.argv", args), patch.dict("os.environ", {"GITHUB_OUTPUT": str(Path(directory) / "output")}):
                    upstream.main()
                self.assertEqual((Path(directory) / "output").read_text(), f"changed={expected}\n")

    def test_first_deploy_404_but_server_errors_fail(self):
        for code in (404, 503):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                error = urllib.error.HTTPError("https://example.com/versions.json", code, "test", {}, None)
                with patch.object(upstream, "discover", return_value={}), patch.object(upstream, "fetch", side_effect=error), patch("sys.argv", ["upstream", "--output", directory, "--published-url", error.url]), patch.dict("os.environ", {"GITHUB_OUTPUT": str(Path(directory) / "output")}):
                    if code == 404:
                        upstream.main()
                        self.assertEqual((Path(directory) / "output").read_text(), "changed=true\n")
                    else:
                        with self.assertRaises(urllib.error.HTTPError):
                            upstream.main()
                        self.assertFalse((Path(directory) / "output").exists())


if __name__ == "__main__":
    unittest.main()
