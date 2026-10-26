from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mclite import mods


class ModrinthQueryTests(unittest.TestCase):
    def test_search_builds_facets(self):
        captured = {}

        def fake_fetch(url, **kwargs):
            captured["url"] = url
            return {"hits": [{"project_id": "abc", "title": "Sodium", "description": "d", "downloads": 5}]}

        with mock.patch.object(mods, "fetch_json", fake_fetch):
            hits = mods.ModrinthClient().search("sodium", game_version="1.20.1", loader="fabric")
        self.assertEqual(hits[0].name, "Sodium")
        self.assertIn("project_type%3Amod", captured["url"])
        self.assertIn("versions%3A1.20.1", captured["url"])
        self.assertIn("categories%3Afabric", captured["url"])


class CurseForgeTests(unittest.TestCase):
    def test_newest_file_chosen(self):
        payload = {
            "data": [
                {"fileName": "old.jar", "fileDate": "2024-01-01T00:00:00Z", "downloadUrl": "https://x/old.jar", "fileLength": 1, "hashes": []},
                {"fileName": "new.jar", "fileDate": "2025-06-01T00:00:00Z", "downloadUrl": "https://x/new.jar", "fileLength": 2, "hashes": [{"algo": 1, "value": "a" * 40}]},
            ]
        }
        client = mods.CurseForgeClient("key")
        client._get = lambda path, params=None: payload
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(mods.Downloader, "ensure", lambda self, task, progress=None: True):
                paths = client.install("123", Path(tmp), game_version="1.20.1", loader="forge")
        self.assertEqual(paths[0].name, "new.jar")

    def test_missing_key(self):
        with self.assertRaises(mods.ModError):
            mods.CurseForgeClient("")


if __name__ == "__main__":
    unittest.main()
