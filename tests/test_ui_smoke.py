"""Smoke-test UI construction with a minimal tkinter stub.

The real Tk needs a display; this stub defines only the widget surface the
launcher touches, so any missing attribute or misspelled widget method in
``_build`` fails the test instead of failing on a user's Windows desktop.
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class _Widget:
    def __init__(self, master=None, **kwargs):
        self._value = kwargs.get("value", "")
        self._values = list(kwargs.get("values") or [])
        self._selection = ()

    # geometry / binding / configuration
    def pack(self, *args, **kwargs):
        return None

    def grid(self, *args, **kwargs):
        return None

    def place(self, *args, **kwargs):
        return None

    def bind(self, *args, **kwargs):
        return None

    def configure(self, *args, **kwargs):
        return None

    def config(self, *args, **kwargs):
        return None

    def columnconfigure(self, *args, **kwargs):
        return None

    def rowconfigure(self, *args, **kwargs):
        return None

    # listbox / treeview
    def insert(self, *args, **kwargs):
        if args and isinstance(args[0], str) and args[0] in ("end", "0"):
            pass
        return None

    def delete(self, *args, **kwargs):
        return None

    def curselection(self):
        return self._selection

    def selection(self):
        return self._selection

    def selection_set(self, *args):
        self._selection = tuple(args)

    def index(self, *args):
        return 0

    def get_children(self):
        return ()

    def add(self, *args, **kwargs):
        return None

    def heading(self, *args, **kwargs):
        return None

    def column(self, *args, **kwargs):
        return None

    def see(self, *args, **kwargs):
        return None

    # ttk state / combobox
    def state(self, *args):
        return None

    def current(self, index=None):
        if index is None:
            return 0
        if 0 <= index < len(self._values):
            self._value = self._values[index]
        return None

    def set(self, value=None):
        self._value = value

    def get(self):
        return self._value

    def __setitem__(self, key, value):
        if key == "values":
            self._values = list(value)
        elif key == "value":
            self._value = value

    def __getitem__(self, key):
        if key == "values":
            return self._values
        return self._value

    # misc
    def stop(self):
        return None

    def start(self, *args):
        return None

    def update(self):
        return None

    def update_idletasks(self):
        return None

    def destroy(self):
        return None

    def winfo_children(self):
        return ()

    def attributes(self, *args, **kwargs):
        return None


class _Variable:
    def __init__(self, value=None):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class Tk(_Widget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._after_calls = []

    def title(self, *args):
        return None

    def geometry(self, *args):
        return None

    def minsize(self, *args):
        return None

    def after(self, delay, callback=None, *args):
        if callback is not None:
            self._after_calls.append((delay, callback))
        return None

    def mainloop(self):
        return None

    def protocol(self, *args):
        return None

    def destroy(self):
        return None


def _install_stub() -> None:
    tk = types.ModuleType("tkinter")
    tk.Tk = Tk
    tk.Toplevel = _Widget
    tk.Listbox = _Widget
    tk.Text = _Widget
    tk.StringVar = _Variable
    tk.BooleanVar = _Variable
    tk.END = "end"
    tk.LEFT = "left"
    tk.RIGHT = "right"

    ttk = types.ModuleType("tkinter.ttk")
    for name in (
        "Frame", "Label", "Button", "Entry", "Combobox", "Treeview",
        "Notebook", "Progressbar", "Checkbutton", "LabelFrame", "Scrollbar",
    ):
        setattr(ttk, name, _Widget)
    tk.ttk = ttk

    filedialog = types.ModuleType("tkinter.filedialog")
    filedialog.askopenfilename = lambda *a, **k: ""
    filedialog.askdirectory = lambda *a, **k: ""
    filedialog.asksaveasfilename = lambda *a, **k: ""

    messagebox = types.ModuleType("tkinter.messagebox")
    messagebox.showinfo = lambda *a, **k: None
    messagebox.showerror = lambda *a, **k: None
    messagebox.showwarning = lambda *a, **k: None

    tk.filedialog = filedialog
    tk.messagebox = messagebox
    tk.ttk = ttk

    sys.modules["tkinter"] = tk
    sys.modules["tkinter.ttk"] = ttk
    sys.modules["tkinter.filedialog"] = filedialog
    sys.modules["tkinter.messagebox"] = messagebox


class UiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.original_modules = {name: sys.modules.get(name) for name in (
            "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
            "mclite.ui.app", "mclite.ui",
        )}
        _install_stub()
        sys.modules.pop("mclite.ui.app", None)
        sys.modules.pop("mclite.ui", None)
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        for name, module in self.original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        self.tmp.cleanup()

    def _app(self):
        import mclite.ui.app as ui
        from mclite.config import ConfigStore

        class TestApp(ui.LauncherApp):
            def __init__(self):
                self.background = []
                super().__init__()

            def _async(self, work, done=None, failed=None):
                self.background.append((work, done, failed))

            def after(self, delay, callback=None, *args):
                return None

        # 冒烟测试必须使用独立配置，不能覆盖真实账号或用户的游戏目录。
        store = ConfigStore(Path(self.tmp.name) / "config")
        store.settings.game_dir = str(Path(self.tmp.name) / "game")
        with patch.object(ui, "ConfigStore", return_value=store):
            return TestApp()

    def test_builds_and_scans(self):
        app = self._app()
        self.assertEqual(app.status.get(), "就绪")
        self.assertTrue(hasattr(app, "_installed"))

        game = Path(self.tmp.name) / "game"
        version_dir = game / "versions" / "1.20.1"
        version_dir.mkdir(parents=True)
        (version_dir / "1.20.1.json").write_text(
            json.dumps(
                {
                    "id": "1.20.1",
                    "mainClass": "net.minecraft.client.main.Main",
                    "type": "release",
                }
            ),
            encoding="utf-8",
        )
        app.settings.game_dir = str(game)
        app._refresh_installed()
        self.assertEqual(len(app._installed), 1)
        app._show_version_info()
        self.assertIn("1.20.1", app.version_info.get())

    def test_progress_and_event_pump(self):
        app = self._app()
        app._on_progress("assets", 5, 10)
        app.events.put((lambda _payload: app._status("done"), None))
        app._pump()
        self.assertEqual(app.status.get(), "done")
        app._game_exited(0, Path("x.log"))
        self.assertIn("游戏已退出", app.status.get())

    def test_settings_save_reads_widgets(self):
        app = self._app()
        app.setting_vars["memory_mb"].set("4096")
        app.setting_vars["game_dir"].set(str(Path(self.tmp.name) / "g"))
        app._save_settings()
        self.assertEqual(app.settings.memory_mb, 4096)

    def test_java_defaults_to_matching_game_version(self):
        from mclite.java import JavaInfo

        app = self._app()
        app._java_ready([JavaInfo("j21", "21.0.1", 21), JavaInfo("j17", "17.0.2", 17)])
        self.assertEqual(app.java_combo.get(), "自动匹配游戏版本")
        self.assertEqual(app._chosen_java("1.20.1", 0).major, 17)
        self.assertEqual(app._chosen_java("1.20.6", 0).major, 21)
        self.assertEqual(app._chosen_java("1.20.1", 1).major, 21)

    def test_saving_game_directory_refreshes_version_list(self):
        app = self._app()
        game = Path(self.tmp.name) / "other-game"
        folder = game / "versions" / "tiny"
        folder.mkdir(parents=True)
        (folder / "tiny.json").write_text('{"id":"tiny"}', encoding="utf-8")
        app.setting_vars["game_dir"].set(str(game))
        app._save_settings()
        self.assertEqual([v.version_id for v in app._installed], ["tiny"])


if __name__ == "__main__":
    unittest.main()
