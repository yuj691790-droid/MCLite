from __future__ import annotations

import unittest
from unittest import mock

from mclite import loaders
from mclite.meta import OsContext, library_downloads


class ForgeOrderingTests(unittest.TestCase):
    def test_bmclapi_list_sorted_newest_first(self):
        payload = [
            {"version": "47.4.2"},
            {"version": "47.4.10"},
            {"version": "47.4.1"},
        ]
        with mock.patch.object(loaders, "fetch_json", return_value=payload):
            self.assertEqual(loaders.forge_versions("1.20.1")[:3], ["47.4.10", "47.4.2", "47.4.1"])

    def test_maven_xml_strips_prefix_and_sorts(self):
        xml = b"""<metadata><versioning><versions>
            <version>1.20.1-47.4.2</version>
            <version>1.20.0-47.0.0</version>
            <version>1.20.1-47.4.10</version>
        </versions></versioning></metadata>"""
        with mock.patch.object(loaders, "fetch_bytes", return_value=xml):
            self.assertEqual(loaders.forge_versions("1.20.1", mirror=False), ["47.4.10", "47.4.2"])


class NeoForgeOrderingTests(unittest.TestCase):
    def test_prefix_and_order(self):
        payload = {"versions": ["21.1.2", "21.1.10", "20.4.5"]}
        with mock.patch.object(loaders, "fetch_json", return_value=payload):
            self.assertEqual(loaders.neoforge_versions("1.21.1"), ["21.1.10", "21.1.2"])
            self.assertEqual(loaders.neoforge_versions("1.20.4"), ["20.4.5"])
            self.assertEqual(loaders.neoforge_versions("1.20.1"), [])


class NativeSelectionTests(unittest.TestCase):
    def test_modern_natives_pick_matching_arch(self):
        libs = [
            {"name": "org.lwjgl:lwjgl:3.3.1:natives-windows", "downloads": {"artifact": {"path": "a/natives-windows.jar", "url": "https://x/a.jar"}}},
            {"name": "org.lwjgl:lwjgl:3.3.1:natives-windows-arm64", "downloads": {"artifact": {"path": "a/natives-windows-arm64.jar", "url": "https://x/b.jar"}}},
            {"name": "org.lwjgl:lwjgl:3.3.1:natives-windows-x86", "downloads": {"artifact": {"path": "a/natives-windows-x86.jar", "url": "https://x/c.jar"}}},
            {"name": "org.lwjgl:lwjgl:3.3.1", "downloads": {"artifact": {"path": "a/lwjgl.jar", "url": "https://x/d.jar"}}},
        ]
        artifacts, natives = library_downloads(libs, OsContext(arch="x86_64"))
        self.assertEqual([p for p, _ in natives], ["a/natives-windows.jar"])
        self.assertEqual([p for p, _ in artifacts], ["a/lwjgl.jar"])

    def test_arm64_choice(self):
        libs = [
            {"name": "org.lwjgl:lwjgl:3.3.1:natives-windows", "downloads": {"artifact": {"path": "plain.jar", "url": "https://x/a.jar"}}},
            {"name": "org.lwjgl:lwjgl:3.3.1:natives-windows-arm64", "downloads": {"artifact": {"path": "arm64.jar", "url": "https://x/b.jar"}}},
        ]
        _, natives = library_downloads(libs, OsContext(arch="arm64"))
        self.assertEqual([p for p, _ in natives], ["arm64.jar"])

    def test_old_style_natives(self):
        libs = [
            {
                "name": "org.lwjgl.lwjgl:lwjgl-platform:2.9.1",
                "natives": {"windows": "natives-windows"},
                "downloads": {
                    "classifiers": {
                        "natives-windows": {"path": "old/natives-windows.jar", "url": "https://x/n.jar"}
                    }
                },
            }
        ]
        artifacts, natives = library_downloads(libs, OsContext())
        self.assertEqual(artifacts, [])
        self.assertEqual([p for p, _ in natives], ["old/natives-windows.jar"])


if __name__ == "__main__":
    unittest.main()


class InstallerHelperTests(unittest.TestCase):
    def test_launcher_profiles_created(self):
        import json
        import tempfile
        from pathlib import Path

        from mclite.loaders import _ensure_launcher_profiles

        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            _ensure_launcher_profiles(game)
            data = json.loads((game / "launcher_profiles.json").read_text())
            self.assertEqual(data["profiles"], {})
            # existing file is never overwritten
            (game / "launcher_profiles.json").write_text("{}")
            _ensure_launcher_profiles(game)
            self.assertEqual((game / "launcher_profiles.json").read_text(), "{}")

    def test_find_generated_version_prefers_newest(self):
        import json
        import tempfile
        from pathlib import Path

        from mclite.loaders import _find_generated_version

        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            for name, ts in (("1.20.1-forge-47.1.0", 100), ("1.20.1-forge-47.2.0", 200), ("other-1.0", 300)):
                folder = game / "versions" / name
                folder.mkdir(parents=True)
                (folder / f"{name}.json").write_text(
                    json.dumps({"id": name, "inheritsFrom": "1.20.1" if "forge" in name else "1.19"})
                )
                import os

                os.utime(folder / f"{name}.json", (ts, ts))
            self.assertEqual(_find_generated_version(game, "1.20.1", "forge"), "1.20.1-forge-47.2.0")
