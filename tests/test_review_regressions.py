"""代码审查中发现的启动链路回归用例；全部使用本地夹具。"""

import tempfile
import unittest
import zipfile
from pathlib import Path

from mclite.installer import InstallError, Installer, InstallResult
from mclite.java import JavaInfo, required_java_major
from mclite.launch import AuthData, build_command
from mclite.meta import OsContext, Version, library_downloads


class ReviewRegressions(unittest.TestCase):
    def test_metadata_java_requirement_overrides_version_guess(self):
        self.assertEqual(required_java_major("26.3", {"javaVersion": {"majorVersion": 25}}), 25)
        self.assertEqual(required_java_major("snapshot", {"javaVersion": {"majorVersion": 21}}), 21)

    def test_legacy_library_keeps_main_jar_and_native(self):
        main = {"path": "lwjgl.jar", "url": "https://example.com/main"}
        native = {"path": "native.jar", "url": "https://example.com/native"}
        lib = {"name": "org.lwjgl:lwjgl:2.9.4", "natives": {"windows": "natives-windows"},
               "downloads": {"artifact": main, "classifiers": {"natives-windows": native}}}
        artifacts, natives = library_downloads([lib], OsContext())
        self.assertEqual(artifacts, [("lwjgl.jar", main)])
        self.assertEqual(natives, [("native.jar", native)])
        artifacts, natives = library_downloads([lib], OsContext(name="linux"))
        self.assertEqual(artifacts, [("lwjgl.jar", main)])
        self.assertEqual(natives, [])

    def test_native_archive_rejects_sibling_prefix_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            archive = game / "libraries" / "native.jar"
            archive.parent.mkdir()
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../natives-other/escape.dll", b"escape")
            version = Version("tiny", {"libraries": [
                {"name": "a:b:1:natives-windows", "downloads": {
                    "artifact": {"path": "native.jar", "url": "https://example.com/native"}}}]})
            with self.assertRaisesRegex(InstallError, "越界"):
                Installer(game)._extract_natives(version)
            self.assertFalse((game / "versions/tiny/natives-other/escape.dll").exists())

    def test_logging_config_is_downloaded_and_used_before_main_class(self):
        class LocalDownloader:
            def ensure(self, task):
                task.dest.parent.mkdir(parents=True, exist_ok=True)
                task.dest.write_text("<Configuration/>", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            version = Version("tiny", {"mainClass": "Main", "logging": {"client": {
                "argument": "-Dlog4j.configurationFile=${path}",
                "file": {"id": "client-1.12.xml", "url": "https://example.com/log.xml"}}}})
            Installer(game, downloader=LocalDownloader())._install_logging(version, InstallResult("tiny"))
            log_path = game / "assets/log_configs/client-1.12.xml"
            self.assertTrue(log_path.is_file())
            spec = build_command(version, game, JavaInfo("java", "17.0.2", 17), AuthData("Steve", "0", "0"))
            argument = f"-Dlog4j.configurationFile={log_path}"
            self.assertIn(argument, spec.command)
            self.assertLess(spec.command.index(argument), spec.command.index("Main"))


if __name__ == "__main__":
    unittest.main()
