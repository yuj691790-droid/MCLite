from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mclite import net
from mclite.auth import _jwt_xuid
from mclite.installer import Installer
from mclite.meta import OsContext, Version, asset_tasks


class AssetTaskTests(unittest.TestCase):
    def test_mirror_first(self):
        index = {"objects": {"a.txt": {"hash": "aa" + "b" * 38, "size": 3}}}
        with tempfile.TemporaryDirectory() as tmp:
            tasks = asset_tasks(index, Path(tmp), OsContext(), mirror=True)
            self.assertEqual(len(tasks), 1)
            self.assertTrue(tasks[0].url.startswith("https://bmclapi2.bangbang93.com/assets/"))
            self.assertTrue(tasks[0].fallback[0].startswith("https://resources.download.minecraft.net/"))
            tasks = asset_tasks(index, Path(tmp), OsContext(), mirror=False)
            self.assertTrue(tasks[0].url.startswith("https://resources.download.minecraft.net/"))
            self.assertEqual(tasks[0].fallback, ())


class NativeExtractionTests(unittest.TestCase):
    def test_extracts_and_skips_meta_inf(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            lib = game / "libraries" / "n" / "natives-windows.jar"
            lib.parent.mkdir(parents=True)
            with zipfile.ZipFile(lib, "w") as zf:
                zf.writestr("a.dll", b"dll")
                zf.writestr("sub/b.dll", b"dll2")
                zf.writestr("META-INF/MANIFEST.MF", b"x")
            raw = {
                "id": "1.20.1",
                "libraries": [
                    {
                        "name": "n.n:n:1.0:natives-windows",
                        "downloads": {"artifact": {"path": "n/natives-windows.jar", "url": "https://x"}},
                    }
                ],
            }
            Installer(game)._extract_natives(Version(id="1.20.1", raw=raw))
            natives = game / "versions" / "1.20.1" / "natives"
            self.assertTrue((natives / "a.dll").is_file())
            self.assertTrue((natives / "sub" / "b.dll").is_file())
            self.assertFalse((natives / "META-INF").exists())


class JwtTests(unittest.TestCase):
    def test_xuid_claim(self):
        import base64

        payload = base64.urlsafe_b64encode(json.dumps({"xuid": "12345"}).encode()).decode().rstrip("=")
        token = f"eyJhbGciOiJub25lIn0.{payload}.sig"
        self.assertEqual(_jwt_xuid(token), "12345")
        self.assertEqual(_jwt_xuid("not-a-jwt"), "")


class DownloaderTests(unittest.TestCase):
    def test_ensure_skips_matching_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x.bin"
            dest.write_bytes(b"hello")
            task = net.DownloadTask(url="https://invalid.example/x", dest=dest, sha1=net.sha1_file(dest), size=5)
            self.assertFalse(net.Downloader().ensure(task))

    def test_mirror_candidates(self):
        urls = net.candidate_urls("https://libraries.minecraft.net/a.jar", True)
        self.assertEqual(urls[0], "https://bmclapi2.bangbang93.com/maven/a.jar")
        self.assertEqual(urls[1], "https://libraries.minecraft.net/a.jar")


if __name__ == "__main__":
    unittest.main()


class InstallFailureTests(unittest.TestCase):
    def test_missing_library_raises(self):
        from mclite.installer import InstallError, InstallResult, Installer
        from mclite.meta import Version

        class FailingDownloader:
            def ensure_all(self, tasks, progress=None):
                return [net.DownloadError("boom")]

            def ensure(self, task, progress=None):
                raise net.DownloadError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            raw = {
                "id": "x",
                "libraries": [
                    {
                        "name": "a.b:c:1.0",
                        "downloads": {"artifact": {"path": "a/c.jar", "url": "https://x/c.jar"}},
                    }
                ],
                "assetIndex": {"id": "1", "url": "https://x/index.json"},
            }
            installer = Installer(game, downloader=FailingDownloader())
            with self.assertRaises(InstallError):
                installer._install_libraries(Version(id="x", raw=raw), None, InstallResult("x"))

    def test_launch_rejects_missing_classpath(self):
        from mclite.launch import LaunchError, LaunchSpec, launch

        spec = LaunchSpec(command=["/bin/true"], cwd=Path("/"), natives_dir=Path("/"), main_class="X", classpath=["/nonexistent/a.jar"])
        with self.assertRaises(LaunchError):
            launch(spec)


class AssetInstallTests(unittest.TestCase):
    def test_assets_and_virtual_mapping(self):
        import json as _json

        from mclite import installer as installer_mod
        from mclite.installer import InstallResult, Installer

        digest = "ab" + "c" * 38
        index = {"objects": {"sounds/a.ogg": {"hash": digest, "size": 3}}, "virtual": True}

        class FakeDownloader:
            def ensure_all(self, tasks, progress=None):
                total = sum((t.size or 0) for t in tasks)
                done = 0
                for task in tasks:
                    task.dest.parent.mkdir(parents=True, exist_ok=True)
                    task.dest.write_bytes(b"abc")
                    done += task.size or 0
                    if progress:
                        progress(done, total)
                return []

            def ensure(self, task, progress=None):
                task.dest.parent.mkdir(parents=True, exist_ok=True)
                task.dest.write_bytes(b"abc")
                return True

        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            original = installer_mod.fetch_json_bytes
            installer_mod.fetch_json_bytes = lambda url: _json.dumps(index).encode()
            try:
                version = Version(
                    id="x",
                    raw={"id": "x", "assetIndex": {"id": "test", "url": "https://example.com/index.json"}},
                )
                result = InstallResult("x")
                Installer(game, downloader=FakeDownloader())._install_assets(version, None, result)
            finally:
                installer_mod.fetch_json_bytes = original

            self.assertTrue((game / "assets" / "indexes" / "test.json").is_file())
            self.assertTrue((game / "assets" / "objects" / digest[:2] / digest).is_file())
            self.assertTrue((game / "assets" / "virtual" / "test" / "sounds" / "a.ogg").is_file())
