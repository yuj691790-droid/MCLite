from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mclite import installer as installer_mod
from mclite.installer import Installer


class FakeDownloader:
    """Writes deterministic bytes; native jars become real zip archives."""

    def __init__(self):
        self.tasks = []

    def _write(self, task):
        task.dest.parent.mkdir(parents=True, exist_ok=True)
        if "natives" in task.dest.name:
            with zipfile.ZipFile(task.dest, "w") as zf:
                zf.writestr("a.dll", b"dll")
                zf.writestr("META-INF/MANIFEST.MF", b"x")
        else:
            task.dest.write_bytes(b"jar-bytes")
        self.tasks.append(task.dest)

    def ensure(self, task, progress=None):
        self._write(task)
        if progress:
            progress(4)
        return True

    def ensure_all(self, tasks, progress=None):
        for task in tasks:
            self._write(task)
        if progress:
            progress(len(self.tasks), len(self.tasks))
        return []


class InstallFlowTests(unittest.TestCase):
    def _version(self, game: Path, vid: str, *, inherits=None, client=True, native=True):
        folder = game / "versions" / vid
        folder.mkdir(parents=True)
        raw = {
            "id": vid,
            "mainClass": "net.minecraft.client.main.Main",
            "assetIndex": {"id": "idx", "url": "https://example.com/idx.json"},
            "assets": "idx",
            "libraries": [
                {
                    "name": "a.b:c:1.0",
                    "downloads": {"artifact": {"path": "a/c-1.0.jar", "url": "https://x/c.jar", "size": 9}},
                }
            ],
        }
        if inherits:
            raw["inheritsFrom"] = inherits
        if client:
            raw["downloads"] = {"client": {"url": "https://x/client.jar", "sha1": "0" * 40, "size": 9}}
        if native:
            raw["libraries"].append(
                {
                    "name": "n.n:n:1.0:natives-windows",
                    "downloads": {"artifact": {"path": "n/n-1.0-natives-windows.jar", "url": "https://x/n.jar"}},
                }
            )
        (folder / f"{vid}.json").write_text(json.dumps(raw), encoding="utf-8")

    def test_full_vanilla_install(self):
        index = {"objects": {"a.txt": {"hash": "ab" + "c" * 38, "size": 3}}}
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            self._version(game, "tiny")
            original = installer_mod.fetch_json_bytes
            installer_mod.fetch_json_bytes = lambda url: json.dumps(index).encode()
            try:
                result = Installer(game, downloader=FakeDownloader()).install("tiny")
            finally:
                installer_mod.fetch_json_bytes = original

            self.assertTrue((game / "versions" / "tiny" / "tiny.jar").is_file())
            self.assertTrue((game / "libraries" / "a" / "c-1.0.jar").is_file())
            self.assertTrue((game / "versions" / "tiny" / "natives" / "a.dll").is_file())
            self.assertFalse((game / "versions" / "tiny" / "natives" / "META-INF").exists())
            self.assertTrue((game / "assets" / "indexes" / "idx.json").is_file())
            self.assertTrue((game / "assets" / "objects" / "ab" / ("ab" + "c" * 38)).is_file())
            self.assertEqual(result.version_id, "tiny")

    def test_inherited_profile_uses_parent_jar(self):
        index = {"objects": {}}
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            self._version(game, "base")
            self._version(game, "child", inherits="base", client=False, native=False)
            original = installer_mod.fetch_json_bytes
            installer_mod.fetch_json_bytes = lambda url: json.dumps(index).encode()
            try:
                result = Installer(game, downloader=FakeDownloader()).install("child")
            finally:
                installer_mod.fetch_json_bytes = original

            self.assertTrue((game / "versions" / "base" / "base.jar").is_file())
            self.assertFalse((game / "versions" / "child" / "child.jar").exists())
            # Natives are extracted into the profile that will actually launch
            # (the merged child), not into the parent version directory.
            self.assertTrue((game / "versions" / "child" / "natives" / "a.dll").is_file())
            self.assertEqual(result.version_id, "child")


if __name__ == "__main__":
    unittest.main()
