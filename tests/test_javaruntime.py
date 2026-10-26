from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from mclite import javaruntime


class ReleaseTests(unittest.TestCase):
    def test_parse_adoptium_payload(self):
        payload = [
            {
                "binary": {
                    "package": {
                        "link": "https://example.com/jre.zip",
                        "name": "OpenJDK21U-jre_x64_windows_hotspot_21.0.5_11.zip",
                        "size": 123,
                        "checksum": "a" * 64,
                    }
                },
                "version": {"semver": "21.0.5+11", "major": 21},
            }
        ]
        with mock.patch.object(javaruntime, "fetch_json", return_value=payload):
            release = javaruntime.fetch_java_release(21)
        self.assertEqual(release.version, "21.0.5+11")
        self.assertEqual(release.url, "https://example.com/jre.zip")
        self.assertEqual(release.sha256, "a" * 64)

    def test_empty_payload(self):
        with mock.patch.object(javaruntime, "fetch_json", return_value=[]):
            with self.assertRaises(javaruntime.JavaRuntimeError):
                javaruntime.fetch_java_release(21)


class ExtractionTests(unittest.TestCase):
    def test_extracts_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "jre.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("jdk-21/bin/java", b"#!/bin/sh\n")
            target = root / "out"
            javaruntime._extract_zip(archive, target)
            self.assertTrue((target / "jdk-21" / "bin" / "java").is_file())

            evil = root / "evil.zip"
            with zipfile.ZipFile(evil, "w") as zf:
                zf.writestr("../escape.txt", b"x")
            with self.assertRaises(javaruntime.JavaRuntimeError):
                javaruntime._extract_zip(evil, root / "out2")


class InstallTests(unittest.TestCase):
    def test_install_returns_java_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive = base / "cache" / "java" / "jre.zip"
            archive.parent.mkdir(parents=True)
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("jdk-21/bin/java.exe", b"exe")
                zf.writestr("jdk-21/bin/java", b"sh")

            class FakeDownloader:
                def ensure(self, task, progress=None):
                    task.dest.parent.mkdir(parents=True, exist_ok=True)
                    task.dest.write_bytes(archive.read_bytes())
                    if progress:
                        progress(task.dest.stat().st_size)
                    return True

            release = javaruntime.JavaRelease(
                major=21,
                version="21.0.5+11",
                url="https://example.com/jre.zip",
                filename="jre.zip",
                size=archive.stat().st_size,
            )
            with mock.patch.object(javaruntime, "fetch_java_release", return_value=release):
                path = javaruntime.install_java(21, base, downloader=FakeDownloader())
            self.assertTrue(path.is_file())
            self.assertIn("bin", path.parts)
            self.assertIn("jre-21-21.0.5_11", str(path))


if __name__ == "__main__":
    unittest.main()


class InstallFailureTests(unittest.TestCase):
    def test_corrupt_archive_leaves_no_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)

            class FakeDownloader:
                def ensure(self, task, progress=None):
                    task.dest.parent.mkdir(parents=True, exist_ok=True)
                    task.dest.write_bytes(b"not a zip")
                    return True

            release = javaruntime.JavaRelease(
                major=21, version="21.1", url="https://x/jre.zip", filename="jre.zip", size=9
            )
            with mock.patch.object(javaruntime, "fetch_java_release", return_value=release):
                with self.assertRaises(Exception):
                    javaruntime.install_java(21, base, downloader=FakeDownloader())
            self.assertFalse((base / "java" / "jre-21-21.1").exists())
