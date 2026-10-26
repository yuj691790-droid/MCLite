from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from mclite.auth import offline_uuid
from mclite.cli import cmd_launch
from mclite.config import ConfigStore
from mclite.java import JavaInfo


class OfflineUuidTests(unittest.TestCase):
    def test_deterministic_and_version_bits(self):
        first = offline_uuid("Steve")
        self.assertEqual(first, offline_uuid("Steve"))
        self.assertNotEqual(first, offline_uuid("Alex"))
        self.assertEqual(len(first), 36)
        self.assertEqual(first[14], "3")
        self.assertIn(first[19], "89ab")


class LaunchDryRunTests(unittest.TestCase):
    def test_prints_command_without_starting(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = Path(tmp) / "game"
            folder = game / "versions" / "1.20.1"
            folder.mkdir(parents=True)
            (folder / "1.20.1.json").write_text(
                json.dumps(
                    {
                        "id": "1.20.1",
                        "mainClass": "net.minecraft.client.main.Main",
                        "assetIndex": {"id": "5", "url": "https://x"},
                        "libraries": [],
                        "arguments": {"jvm": ["-cp", "${classpath}"], "game": ["--username", "${auth_player_name}"]},
                    }
                ),
                encoding="utf-8",
            )
            store = ConfigStore(Path(tmp) / "config")
            store.settings.game_dir = str(game)
            args = Namespace(version="1.20.1", offline="Steve", dry_run=True, wait=False)

            buffer = io.StringIO()
            # dry-run 只验证命令生成，不依赖宿主机器的 Java 安装。
            with contextlib.redirect_stdout(buffer), patch(
                "mclite.cli.find_javas", return_value=[JavaInfo("java", "17.0.2", 17)]
            ):
                code = cmd_launch(args, store)

            self.assertEqual(code, 0)
            output = buffer.getvalue()
            self.assertIn("net.minecraft.client.main.Main", output)
            self.assertIn("--username Steve", output)


if __name__ == "__main__":
    unittest.main()
