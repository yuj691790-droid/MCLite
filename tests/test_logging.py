from __future__ import annotations

import logging
import tempfile
import sys
import threading
import unittest
from pathlib import Path

from mclite.logging_setup import log_exception, setup_logging


class LoggingTests(unittest.TestCase):
    def test_writes_exception_and_cleans_up(self):
        root = logging.getLogger()
        old_handlers = list(root.handlers)
        old_level = root.level
        old_hooks = sys.excepthook, threading.excepthook
        with tempfile.TemporaryDirectory() as tmp:
            path = setup_logging(Path(tmp))
            try:
                logger = logging.getLogger("mclite.test")
                try:
                    raise ValueError("boom")
                except ValueError as exc:
                    log_exception(logger, "operation failed", exc)
                for handler in root.handlers:
                    handler.flush()
                text = path.read_text(encoding="utf-8")
                self.assertIn("operation failed", text)
                self.assertIn("ValueError: boom", text)
                self.assertIn("Traceback", text)
                self.assertEqual(path.relative_to(tmp), Path("logs") / "mclite.log")
            finally:
                # Windows 禁止删除仍被打开的日志文件，必须先关闭再清理临时目录。
                for handler in list(root.handlers):
                    if handler not in old_handlers:
                        root.removeHandler(handler)
                        handler.close()
                root.setLevel(old_level)
                sys.excepthook, threading.excepthook = old_hooks


if __name__ == "__main__":
    unittest.main()
