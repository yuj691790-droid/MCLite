import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mclite.config import default_base_dir
from mclite.logging_setup import setup_logging

LOG_PATH = setup_logging(default_base_dir())

try:
    from mclite.ui.app import main
except Exception:  # noqa: BLE001 - windowed process: report where the log is
    import logging

    logging.getLogger("mclite").exception("MCLite failed to start")
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            0,
            f"MCLite 启动失败。\n\n日志：{LOG_PATH}\n\n请把日志内容发给开发者。",
            "MCLite",
            0x10,
        )
    except Exception:  # noqa: BLE001
        pass
    raise

if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001
        import logging
        import traceback

        logging.getLogger("mclite").exception("MCLite crashed")
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"MCLite 遇到错误并退出。\n\n日志：{LOG_PATH}\n\n{traceback.format_exc()[-800:]}",
                "MCLite",
                0x10,
            )
        except Exception:  # noqa: BLE001
            pass
        raise
