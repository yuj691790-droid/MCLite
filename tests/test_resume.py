from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclite import net


class FakeResponse:
    def __init__(self, status: int, data: bytes):
        self.status = status
        self._data = data
        self._pos = 0

    def read(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ResumeTests(unittest.TestCase):
    def test_resumes_from_partial_file(self):
        full = b"abcdef"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.bin"
            part = dest.with_name("file.bin.part")
            part.write_bytes(b"abc")
            task = net.DownloadTask(url="http://x/f", dest=dest, sha1=net.sha1_bytes(full), size=len(full))
            seen = {}

            def fake_open(url, timeout, headers=None):
                seen["range"] = (headers or {}).get("Range")
                return FakeResponse(206, b"def")

            with mock.patch.object(net, "_open", fake_open):
                net.Downloader()._download_one("http://x/f", dest, task, None)

            self.assertEqual(seen["range"], "bytes=3-")
            self.assertEqual(dest.read_bytes(), full)

    def test_server_ignoring_range_restarts(self):
        full = b"abcdef"
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.bin"
            dest.with_name("file.bin.part").write_bytes(b"abc")
            task = net.DownloadTask(url="http://x/f", dest=dest, sha1=net.sha1_bytes(full), size=len(full))

            with mock.patch.object(net, "_open", lambda url, timeout, headers=None: FakeResponse(200, full)):
                net.Downloader()._download_one("http://x/f", dest, task, None)

            self.assertEqual(dest.read_bytes(), full)

    def test_corrupt_partial_is_discarded(self):
        full = b"abcdef"
        responses = [FakeResponse(200, b"xxxxxx"), FakeResponse(200, full)]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "file.bin"
            task = net.DownloadTask(url="http://x/f", dest=dest, sha1=net.sha1_bytes(full), size=len(full))

            with mock.patch.object(net, "_open", lambda *a, **k: responses.pop(0)):
                net.Downloader(retries=2). _download_one("http://x/f", dest, task, None)

            self.assertEqual(dest.read_bytes(), full)


if __name__ == "__main__":
    unittest.main()
