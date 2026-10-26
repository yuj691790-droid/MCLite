"""File logging for the GUI/CLI, including uncaught-exception hooks.

A windowed (``.pyw``) process has no console, so a crash otherwise leaves the
user with nothing to report. Everything goes to ``<base>/logs/mclite.log``.
"""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "mclite"


def setup_logging(base_dir: Path, *, level: int = logging.INFO) -> Path:
    """Install a rotating file handler and process/thread exception hooks."""
    log_dir = Path(base_dir) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "mclite.log"

    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers when setup runs twice (tests, or re-entry).
    for existing in list(root.handlers):
        if isinstance(existing, RotatingFileHandler) and getattr(existing, "baseFilename", "") == str(path):
            root.removeHandler(existing)
    root.addHandler(handler)

    if sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(stream)

    def handle_exception(exc_type, exc_value, exc_tb):
        logging.getLogger(LOGGER_NAME).critical(
            "uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )
        if sys.__excepthook__ is not None:
            sys.__excepthook__(exc_type, exc_value, exc_tb)

    def handle_thread(args: threading.ExceptHookArgs) -> None:
        logging.getLogger(LOGGER_NAME).critical(
            "thread %s crashed",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread
    return path


def log_exception(logger: logging.Logger, message: str, exc: BaseException) -> None:
    logger.error(message, exc_info=(type(exc), exc, exc.__traceback__))
