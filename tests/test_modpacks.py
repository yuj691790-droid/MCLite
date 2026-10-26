from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from mclite import modpacks
from mclite.net import Downloader, sha1_file


def build_pack(root: Path, *, bad_path: bool = False) -> Path:
    mod = root / "source-mod.jar"
    mod.write_bytes(b"fake-mod-bytes")
    digest = sha1_file(mod)
    files = [
        {
            "path": "../evil.jar" if bad_path else "mods/example.jar",
            "hashes": {"sha1": digest},
            "downloads": [mod.as_uri()],
            "fileSize": mod.stat().st_size,
        }
    ]
    index = {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": "1.2.3",
        "name": "Test Pack",
        "files": files,
        "dependencies": {"minecraft": "1.20.1", "fabric-loader": "0.15.11"},
    }
    pack = root / "pack.mrpack"
    with zipfile.ZipFile(pack, "w") as zf:
        zf.writestr("modrinth.index.json", json.dumps(index))
        zf.writestr("overrides/config/a.txt", "base")
        zf.writestr("client-overrides/config/a.txt", "client")
        zf.writestr("overrides/readme.md", "hi")
    return pack


class MrpackTests(unittest.TestCase):
    def test_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = build_pack(Path(tmp))
            index = modpacks.parse_mrpack(pack)
            self.assertEqual(index.name, "Test Pack")
            self.assertEqual(index.mc_version, "1.20.1")
            self.assertEqual(index.loader, "fabric")
            self.assertEqual(index.loader_version, "0.15.11")
            self.assertEqual(len(index.files), 1)

    def test_install_files_and_overrides(self):
        calls = {}

        def fake_fabric(mc, loader, dest, **kwargs):
            calls["fabric"] = (mc, loader, Path(dest))
            return "fabric-loader-x"

        original = modpacks.install_fabric
        modpacks.install_fabric = fake_fabric
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pack = build_pack(root)
                dest = modpacks.install_mrpack(pack, root / "instances", downloader=Downloader())
                self.assertEqual((dest / "mods" / "example.jar").read_bytes(), b"fake-mod-bytes")
                self.assertEqual((dest / "config" / "a.txt").read_text(), "client")
                self.assertEqual((dest / "readme.md").read_text(), "hi")
                self.assertEqual(calls["fabric"], ("1.20.1", "0.15.11", dest))
        finally:
            modpacks.install_fabric = original

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = build_pack(Path(tmp), bad_path=True)
            with self.assertRaises(modpacks.ModpackError):
                modpacks.install_mrpack(pack, Path(tmp) / "instances", downloader=Downloader())

    def test_rejects_non_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "x.mrpack"
            with zipfile.ZipFile(bad, "w") as zf:
                zf.writestr("hello.txt", "hi")
            with self.assertRaises(modpacks.ModpackError):
                modpacks.parse_mrpack(bad)


if __name__ == "__main__":
    unittest.main()


class CFModpackTests(unittest.TestCase):
    def _build(self, root: Path) -> tuple[Path, Path]:
        mod = root / "cf-mod.jar"
        mod.write_bytes(b"cf-mod")
        manifest = {
            "manifestType": "minecraftModpack",
            "manifestVersion": 1,
            "name": "CF Pack",
            "version": "2.0",
            "minecraft": {"version": "1.20.1", "modLoaders": [{"id": "fabric-0.15.11", "primary": True}]},
            "files": [{"projectID": 11, "fileID": 22, "required": True}],
            "overrides": "overrides",
        }
        pack = root / "pack.zip"
        with zipfile.ZipFile(pack, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest))
            zf.writestr("overrides/config/x.txt", "cfg")
        return pack, mod

    def test_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack, _ = self._build(Path(tmp))
            index = modpacks.parse_cfpack(pack)
            self.assertEqual(index.name, "CF Pack")
            self.assertEqual(index.mc_version, "1.20.1")
            self.assertEqual(index.loader, "fabric")
            self.assertEqual(index.loader_version, "0.15.11")
            self.assertEqual(index.files[0].project_id, 11)

    def test_install(self):
        calls = {}

        def fake_fabric(mc, loader, dest, **kwargs):
            calls["fabric"] = (mc, loader, Path(dest))
            return "fabric-x"

        original = modpacks.install_fabric
        modpacks.install_fabric = fake_fabric
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                pack, mod = self._build(root)
                digest = sha1_file(mod)

                class FakeClient:
                    def file_info(self, project_id, file_id):
                        return {
                            "fileName": "cf-mod.jar",
                            "downloadUrl": mod.as_uri(),
                            "fileLength": mod.stat().st_size,
                            "hashes": [{"algo": 1, "value": digest}],
                        }

                dest = modpacks.install_cfpack(
                    pack,
                    root / "instances",
                    api_key="key",
                    client=FakeClient(),
                    downloader=Downloader(),
                )
                self.assertEqual((dest / "mods" / "cf-mod.jar").read_bytes(), b"cf-mod")
                self.assertEqual((dest / "config" / "x.txt").read_text(), "cfg")
                self.assertEqual(calls["fabric"], ("1.20.1", "0.15.11", dest))
        finally:
            modpacks.install_fabric = original

    def test_disabled_download_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack, _ = self._build(root)

            class NoUrlClient:
                def file_info(self, project_id, file_id):
                    return {"fileName": "x.jar", "downloadUrl": None}

            with self.assertRaises(modpacks.ModpackError):
                modpacks.install_cfpack(pack, root / "instances", api_key="k", client=NoUrlClient())

    def test_loader_parsing(self):
        self.assertEqual(modpacks._parse_cf_loader("forge-47.2.0"), ("forge", "47.2.0"))
        self.assertEqual(modpacks._parse_cf_loader("neoforge-20.4.253"), ("neoforge", "20.4.253"))
        self.assertEqual(modpacks._parse_cf_loader("fabric-0.15.11"), ("fabric", "0.15.11"))
        with self.assertRaises(modpacks.ModpackError):
            modpacks._parse_cf_loader("quilt-1.0")
