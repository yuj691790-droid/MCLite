from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mclite.instances import scan_versions


class ScanTests(unittest.TestCase):
    def _make(self, root: Path, vid: str) -> None:
        folder = root / "versions" / vid
        folder.mkdir(parents=True)
        (folder / f"{vid}.json").write_text(json.dumps({"id": vid}))

    def test_main_and_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp)
            self._make(game, "1.20.1")
            self._make(game / "instances" / "packA", "fabric-loader-0.15-1.20.1")
            self._make(game / "instances" / "packB", "1.19.2")
            (game / "versions" / "broken").mkdir()
            (game / "versions" / "broken" / "notes.txt").write_text("x")

            result = scan_versions(game)
            self.assertEqual([r.label for r in result], [
                "1.20.1",
                "packA / fabric-loader-0.15-1.20.1",
                "packB / 1.19.2",
            ])
            self.assertEqual(result[1].root, game / "instances" / "packA")
            self.assertEqual(result[1].version_id, "fabric-loader-0.15-1.20.1")

    def test_missing_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scan_versions(Path(tmp)), [])


if __name__ == "__main__":
    unittest.main()
