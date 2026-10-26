"""Tkinter UI for MCLite (Windows-first, intentionally plain)."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .. import __version__
from ..auth import AuthSession, DeviceCode, MicrosoftAuth
from ..config import ConfigStore
from ..installer import Installer
from ..instances import scan_versions
from ..java import JavaInfo, find_javas, pick_java, required_java_major
from ..javaruntime import install_java
from ..launch import AuthData, LaunchError, build_command, launch
from ..logging_setup import log_exception
from ..loaders import (
    LoaderError,
    fabric_versions,
    forge_versions,
    install_fabric,
    install_forge,
    install_neoforge,
    neoforge_versions,
)
from ..meta import Manifest, Version
from ..mods import CurseForgeClient, ModrinthClient
from ..modpacks import install_cfpack, install_mrpack

LOADERS = ["原版", "Fabric", "Forge", "NeoForge"]
SOURCES = ["Modrinth", "CurseForge"]


class LauncherApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"MCLite {__version__}")
        self.geometry("1000x660")
        self.minsize(860, 560)

        self.store = ConfigStore()
        self.settings = self.store.settings
        self.events: queue.Queue[tuple[Callable, object]] = queue.Queue()
        self.manifest: Manifest | None = None
        self.javas: list[JavaInfo] = []
        self.session: AuthSession | None = None
        self.busy = False
        self.logger = logging.getLogger("mclite.ui")

        self._build()
        self.after(100, self._pump)
        self._load_accounts()
        self._refresh_installed()
        self._async(self._fetch_manifest, self._manifest_ready)
        self._async(lambda: find_javas(self.settings.java_path), self._java_ready)

    # ------------------------------------------------------------------
    # plumbing
    # ------------------------------------------------------------------
    def _async(self, work: Callable[[], object], done: Callable[[object], None] | None = None,
               failed: Callable[[Exception], None] | None = None) -> None:
        # work 在后台运行，不得读写 Tk；done/failed 由主线程的 _pump 调用。
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("background task failed")
                self.events.put((failed or self._show_error, exc))
            else:
                if done:
                    self.events.put((done, result))

        threading.Thread(target=runner, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                callback, payload = self.events.get_nowait()
                try:
                    callback(payload)
                except Exception as exc:  # noqa: BLE001 - keep the queue alive
                    log_exception(self.logger, "UI callback failed", exc)
                    self._show_error(exc)
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _show_error(self, exc: Exception) -> None:
        log_exception(self.logger, "operation failed", exc)
        self.busy = False
        self.progress.stop()
        self.status.set(str(exc)[:400])
        for name in ("install_button", "play_button"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.state(["!disabled"])
        messagebox.showerror("MCLite", str(exc)[:2000])

    def _status(self, text: str) -> None:
        self.status.set(text)

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build(self) -> None:
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Label(top, text="MCLite", font=("Segoe UI", 15, "bold")).pack(side="left")
        self.account_label = ttk.Label(top, text="未登录")
        self.account_label.pack(side="right")
        ttk.Button(top, text="登录 Microsoft", command=self._login).pack(side="right", padx=6)
        ttk.Button(top, text="切换账号", command=self._switch_account).pack(side="right", padx=6)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 6))
        self._build_launch_tab()
        self._build_download_tab()
        self._build_mods_tab()
        self._build_settings_tab()

        bottom = ttk.Frame(self, padding=(10, 4))
        bottom.pack(fill="x")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self.status).pack(side="left")
        self.progress = ttk.Progressbar(bottom, length=260, mode="determinate")
        self.progress.pack(side="right")

    # -- launch ---------------------------------------------------------
    def _build_launch_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="启动")
        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True)
        ttk.Label(left, text="已安装版本").pack(anchor="w")
        self.version_list = tk.Listbox(left, exportselection=False)
        self.version_list.pack(fill="both", expand=True, pady=4)
        self.version_list.bind("<<ListboxSelect>>", lambda _e: self._show_version_info())
        ttk.Button(left, text="刷新列表", command=self._refresh_installed).pack(anchor="w")

        right = ttk.Frame(tab, padding=(12, 0))
        right.pack(side="right", fill="both", expand=True)
        self.version_info = tk.StringVar(value="选择一个版本")
        ttk.Label(right, textvariable=self.version_info, justify="left").pack(anchor="w", pady=4)
        self.play_button = ttk.Button(right, text="启动游戏", command=self._play)
        self.play_button.pack(anchor="w", pady=10)
        ttk.Label(right, text="Java：").pack(anchor="w")
        self.java_combo = ttk.Combobox(right, state="readonly", width=60)
        self.java_combo.pack(anchor="w", pady=(0, 8))

    # -- download -------------------------------------------------------
    def _build_download_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="下载")
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        ttk.Label(bar, text="显示：").pack(side="left")
        self.type_filter = ttk.Combobox(bar, values=["正式版", "快照", "全部"], state="readonly", width=8)
        self.type_filter.current(0)
        self.type_filter.pack(side="left")
        self.type_filter.bind("<<ComboboxSelected>>", lambda _e: self._fill_versions())

        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True, pady=6)
        self.dl_list = tk.Listbox(body, exportselection=False)
        self.dl_list.pack(side="left", fill="both", expand=True)
        self.dl_list.bind("<<ListboxSelect>>", lambda _e: self._on_download_select())

        side = ttk.Frame(body, padding=(12, 0))
        side.pack(side="right", fill="y")
        ttk.Label(side, text="加载器").pack(anchor="w")
        self.loader_combo = ttk.Combobox(side, values=LOADERS, state="readonly", width=14)
        self.loader_combo.current(0)
        self.loader_combo.pack(anchor="w", pady=4)
        self.loader_combo.bind("<<ComboboxSelected>>", lambda _e: self._load_loader_versions())
        ttk.Label(side, text="加载器版本").pack(anchor="w")
        self.loader_version_combo = ttk.Combobox(side, state="readonly", width=22)
        self.loader_version_combo.pack(anchor="w", pady=4)
        self.install_button = ttk.Button(side, text="下载并安装", command=self._install)
        self.install_button.pack(anchor="w", pady=10)
        self.install_log = tk.Text(side, width=34, height=8, state="disabled")
        self.install_log.pack(fill="both", expand=True)

    # -- mods -----------------------------------------------------------
    def _build_mods_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Mod")
        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        ttk.Label(bar, text="来源").pack(side="left")
        self.mod_source = ttk.Combobox(bar, values=SOURCES, state="readonly", width=12)
        self.mod_source.current(0)
        self.mod_source.pack(side="left", padx=(0, 8))
        self.mod_query = ttk.Entry(bar, width=36)
        self.mod_query.pack(side="left", padx=4)
        self.mod_query.bind("<Return>", lambda _e: self._search_mods())
        ttk.Button(bar, text="搜索", command=self._search_mods).pack(side="left", padx=4)
        ttk.Label(bar, text="游戏版本").pack(side="left", padx=(12, 2))
        self.mod_game_version = ttk.Combobox(bar, width=10)
        self.mod_game_version.pack(side="left")
        ttk.Label(bar, text="加载器").pack(side="left", padx=(8, 2))
        self.mod_loader = ttk.Combobox(bar, values=["fabric", "forge", "neoforge"], state="readonly", width=10)
        self.mod_loader.current(0)
        self.mod_loader.pack(side="left")

        self.mod_results = ttk.Treeview(tab, columns=("name", "downloads"), show="headings", height=12)
        self.mod_results.heading("name", text="名称")
        self.mod_results.heading("downloads", text="下载量")
        self.mod_results.column("name", width=520)
        self.mod_results.column("downloads", width=100, anchor="e")
        self.mod_results.pack(fill="both", expand=True, pady=6)
        self.mod_hits: list = []
        self.mod_results.bind("<<TreeviewSelect>>", lambda _e: self._mod_selected())
        actions = ttk.Frame(tab)
        actions.pack(anchor="w")
        ttk.Button(actions, text="下载所选 Mod", command=self._install_mod).pack(side="left")
        ttk.Button(actions, text="导入整合包 (.mrpack)…", command=self._import_modpack).pack(side="left", padx=8)
        ttk.Button(actions, text="导入 CurseForge 包 (.zip)…", command=self._import_cfpack).pack(side="left")

    # -- settings -------------------------------------------------------
    def _build_settings_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(tab, text="设置")
        rows = ttk.Frame(tab)
        rows.pack(fill="x")
        self.setting_vars = {
            "game_dir": tk.StringVar(value=self.settings.game_dir),
            "java_path": tk.StringVar(value=self.settings.java_path),
            "memory_mb": tk.StringVar(value=str(self.settings.memory_mb)),
            "curseforge_key": tk.StringVar(value=self.settings.curseforge_key),
            "microsoft_client_id": tk.StringVar(value=self.settings.microsoft_client_id),
        }
        labels = [
            ("游戏目录", "game_dir", True),
            ("Java 路径（留空自动检测）", "java_path", True),
            ("内存（MB）", "memory_mb", False),
            ("CurseForge API Key", "curseforge_key", False),
            ("Microsoft 客户端 ID", "microsoft_client_id", False),
        ]
        for index, (label, key, browse) in enumerate(labels):
            ttk.Label(rows, text=label).grid(row=index, column=0, sticky="w", pady=4)
            entry = ttk.Entry(rows, textvariable=self.setting_vars[key], width=60)
            entry.grid(row=index, column=1, sticky="we", pady=4, padx=6)
            if browse:
                ttk.Button(rows, text="浏览…", command=lambda k=key: self._browse(k)).grid(row=index, column=2)
        rows.columnconfigure(1, weight=1)
        self.mirror_var = tk.BooleanVar(value=self.settings.mirror)
        ttk.Checkbutton(rows, text="使用 BMCLAPI 镜像（国内推荐）", variable=self.mirror_var).grid(
            row=len(labels), column=1, sticky="w", pady=4
        )
        java_box = ttk.Frame(rows)
        java_box.grid(row=len(labels) + 1, column=1, sticky="w", pady=4)
        ttk.Label(java_box, text="没有 Java？").pack(side="left")
        ttk.Button(java_box, text="安装 Java 21", command=lambda: self._install_java(21)).pack(side="left", padx=4)
        ttk.Button(java_box, text="安装 Java 17", command=lambda: self._install_java(17)).pack(side="left")
        buttons = ttk.Frame(tab)
        buttons.pack(anchor="w", pady=12)
        ttk.Button(buttons, text="保存设置", command=self._save_settings).pack(side="left")
        ttk.Button(buttons, text="打开日志文件夹", command=self._open_logs).pack(side="left", padx=8)

    def _open_logs(self) -> None:
        path = Path(self.store.base_dir) / "logs"
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            webbrowser.open(path.as_uri())

    # ------------------------------------------------------------------
    # menu actions
    # ------------------------------------------------------------------
    def _browse(self, key: str) -> None:
        if key == "game_dir":
            path = filedialog.askdirectory(initialdir=self.setting_vars[key].get() or str(Path.home()))
        else:
            path = filedialog.askopenfilename(filetypes=[("Java", "java.exe"), ("All", "*.*")])
        if path:
            self.setting_vars[key].set(path)

    def _install_java(self, major: int) -> None:
        self._status(f"下载 Java {major}…")

        def progress(phase: str, done: int, total: int) -> None:
            self.events.put((lambda _p: self._on_progress(phase, done, total), None))

        def work() -> Path:
            return install_java(major, self.store.base_dir, progress=progress)

        def done(path: Path) -> None:
            self.settings.java_path = str(path)
            self.store.save()
            self.setting_vars["java_path"].set(str(path))
            self._status(f"Java 已安装：{path}")
            self._async(lambda: find_javas(str(path)), self._java_ready)

        self._async(work, done)

    def _save_settings(self) -> None:
        self.settings.game_dir = self.setting_vars["game_dir"].get().strip() or self.settings.game_dir
        self.settings.java_path = self.setting_vars["java_path"].get().strip()
        try:
            self.settings.memory_mb = int(self.setting_vars["memory_mb"].get().strip() or "2048")
        except ValueError:
            self.settings.memory_mb = 2048
        self.settings.curseforge_key = self.setting_vars["curseforge_key"].get().strip()
        self.settings.microsoft_client_id = self.setting_vars["microsoft_client_id"].get().strip()
        self.settings.mirror = self.mirror_var.get()
        self.settings.normalized()
        self.store.save()
        self._refresh_installed()
        self._status("设置已保存")
        self._async(lambda: find_javas(self.settings.java_path), self._java_ready)

    # ------------------------------------------------------------------
    # manifest / versions
    # ------------------------------------------------------------------
    def _fetch_manifest(self) -> Manifest:
        return Manifest.fetch(mirror=self.settings.mirror)

    def _chosen_java(self, version_id: str, index: int, metadata: dict | None = None) -> JavaInfo | None:
        if 1 <= index <= len(self.javas):
            return self.javas[index - 1]
        return pick_java(self.javas or find_javas(self.settings.java_path), required_java_major(version_id, metadata))

    def _java_ready(self, javas: list[JavaInfo]) -> None:
        self.javas = javas
        labels = [java.label for java in javas]
        # 默认按游戏版本匹配，避免始终使用列表里最新的 Java。
        self.java_combo["values"] = ["自动匹配游戏版本", *labels]
        self.java_combo.current(0)
        if not labels and not self.settings.java_path:
            self._status("没有检测到 Java，请在设置里指定 java.exe")

    def _manifest_ready(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._fill_versions()
        self._status(f"版本清单已更新，最新正式版 {manifest.latest_release}")

    def _fill_versions(self) -> None:
        if not self.manifest:
            return
        mode = self.type_filter.get()
        self.dl_list.delete(0, "end")
        self._version_ids = []
        for version in self.manifest.versions:
            if mode == "正式版" and version.type != "release":
                continue
            if mode == "快照" and version.type != "snapshot":
                continue
            self._version_ids.append(version.id)
            self.dl_list.insert("end", f"{version.id}    [{version.type}]")

    def _selected_version_id(self) -> str | None:
        selection = self.dl_list.curselection()
        if not selection or not self._version_ids:
            return None
        return self._version_ids[selection[0]]

    def _on_download_select(self) -> None:
        version_id = self._selected_version_id()
        if version_id:
            self.mod_game_version.set(version_id)
            self._load_loader_versions()

    def _load_loader_versions(self) -> None:
        version_id = self._selected_version_id()
        loader = self.loader_combo.get()
        if not version_id or loader == "原版":
            self.loader_version_combo["values"] = []
            self.loader_version_combo.set("")
            return
        self._status(f"获取 {loader} 版本…")

        def fetch() -> list[str]:
            if loader == "Fabric":
                return fabric_versions(version_id, mirror=self.settings.mirror)
            if loader == "Forge":
                return forge_versions(version_id, mirror=self.settings.mirror)
            return neoforge_versions(version_id)

        def ready(versions: list[str]) -> None:
            self.loader_version_combo["values"] = versions[:200]
            self.loader_version_combo.set(versions[0] if versions else "")
            self._status(f"{loader}：{len(versions)} 个可用版本" if versions else f"{loader} 没有 {version_id} 的版本")

        self._async(fetch, ready)

    def _refresh_installed(self) -> None:
        """List versions in the main game dir and in ``instances/<name>``."""
        self.version_list.delete(0, "end")
        self._installed = scan_versions(Path(self.settings.game_dir))
        for entry in self._installed:
            self.version_list.insert("end", entry.label)
        if self._installed:
            self.version_list.selection_set(0)
            self._show_version_info()

    def _selected_installed(self):
        selection = self.version_list.curselection()
        if not selection or not getattr(self, "_installed", None):
            return None
        return self._installed[selection[0]]

    def _show_version_info(self) -> None:
        selected = self._selected_installed()
        if not selected:
            return
        label, root, version_id = selected.label, selected.root, selected.version_id
        try:
            version = Version.load(version_id, root, mirror=self.settings.mirror)
        except Exception as exc:  # noqa: BLE001
            self.version_info.set(f"{label}\n读取失败：{exc}")
            return
        loader = "原版"
        if "fabric" in version.id.lower():
            loader = "Fabric"
        elif "neoforge" in version.id.lower():
            loader = "NeoForge"
        elif "forge" in version.id.lower():
            loader = "Forge"
        self.version_info.set(
            f"{label}\n类型：{version.type}    加载器：{loader}\n主类：{version.main_class}"
        )

    # ------------------------------------------------------------------
    # install
    # ------------------------------------------------------------------
    def _install(self) -> None:
        if self.busy:
            return
        version_id = self._selected_version_id()
        if not version_id:
            messagebox.showinfo("MCLite", "先在上面的列表里选择一个游戏版本。")
            return
        loader = self.loader_combo.get()
        loader_version = self.loader_version_combo.get()
        java_index = self.java_combo.current()
        if loader != "原版" and not loader_version:
            messagebox.showinfo("MCLite", "请选择加载器版本。")
            return
        self.busy = True
        self.install_button.state(["disabled"])
        self.progress["mode"] = "determinate"
        self.progress["value"] = 0
        self._log(f"开始安装 {version_id} / {loader}")

        last = {"time": 0.0, "percent": -1}

        def progress(phase: str, done: int, total: int) -> None:
            percent = int(done * 100 / total) if total else -1
            now = time.time()
            if percent == last["percent"] and now - last["time"] < 0.5 and phase != "metadata":
                return
            last["time"] = now
            last["percent"] = percent
            self.events.put((lambda _p: self._on_progress(phase, done, total), None))

        def work() -> str:
            game_dir = Path(self.settings.game_dir)
            if loader == "原版":
                Installer(game_dir, mirror=self.settings.mirror).install(version_id, progress)
                return version_id
            if loader == "Fabric":
                return install_fabric(version_id, loader_version, game_dir, mirror=self.settings.mirror, progress=progress)
            version = Version.load(version_id, game_dir, mirror=self.settings.mirror)
            java = self._chosen_java(version_id, java_index, version.raw)
            if java is None:
                raise LoaderError("找不到 Java，请先在设置里指定 java.exe。")
            if loader == "Forge":
                return install_forge(version_id, loader_version, game_dir, java, mirror=self.settings.mirror, progress=progress)
            return install_neoforge(version_id, loader_version, game_dir, java, progress=progress)

        def done(new_id: str) -> None:
            self.busy = False
            self.install_button.state(["!disabled"])
            self.settings.last_version = new_id
            self.store.save()
            self._refresh_installed()
            self._log(f"完成：{new_id}")
            self._status(f"安装完成：{new_id}")

        self._async(work, done)

    def _on_progress(self, phase: str, done: int, total: int) -> None:
        if total > 0:
            self.progress["value"] = min(100, done * 100 / total)
        self._status(f"{phase}：{done}/{total}" if total else phase)

    def _log(self, text: str) -> None:
        self.install_log.configure(state="normal")
        self.install_log.insert("end", text + "\n")
        self.install_log.see("end")
        self.install_log.configure(state="disabled")

    # ------------------------------------------------------------------
    # launch
    # ------------------------------------------------------------------
    def _play(self) -> None:
        selected = self._selected_installed()
        if not selected:
            messagebox.showinfo("MCLite", "先选择一个已安装的版本。")
            return
        root, version_id = selected.root, selected.version_id
        java_index = self.java_combo.current()
        if self.session is None:
            messagebox.showinfo("MCLite", "请先登录 Microsoft 账户（正版）。")
            return
        self.busy = True
        self.play_button.state(["disabled"])
        self._status("准备启动…")

        def work() -> object:
            session = self.session
            auth = MicrosoftAuth(self.settings.microsoft_client_id, self.store.accounts_file)
            if session.expired:
                session = auth.refresh(session)
                self.session = session
            version = Version.load(version_id, root, mirror=self.settings.mirror)
            java = self._chosen_java(version_id, java_index, version.raw)
            if java is None:
                raise LaunchError("找不到 Java，请先在设置里指定 java.exe。")
            spec = build_command(
                version,
                root,
                java,
                AuthData(session.username, session.uuid, session.access_token, xuid=session.xuid),
                memory_mb=self.settings.memory_mb,
                extra_jvm_args=self.settings.extra_jvm_args,
            )
            log_path = self.store.base_dir / "logs" / f"game-{version_id}-{time.strftime('%Y%m%d-%H%M%S')}.log"
            return launch(spec, log_path), log_path

        def done(result: object) -> None:
            process, log_path = result
            self.busy = False
            self.play_button.state(["!disabled"])
            self._status(f"游戏已启动，日志：{log_path}")
            threading.Thread(target=self._watch_game, args=(process, log_path), daemon=True).start()

        self._async(work, done)

    def _watch_game(self, process, log_path: Path) -> None:
        code = process.wait()
        self.events.put((lambda _p: self._game_exited(code, log_path), None))

    def _game_exited(self, code: int, log_path: Path) -> None:
        if code == 0:
            self._status("游戏已退出")
        else:
            self._status(f"游戏异常退出（退出码 {code}），日志：{log_path}")
            messagebox.showerror(
                "MCLite",
                f"游戏异常退出（退出码 {code}）。\n\n日志：\n{log_path}",
            )

    # ------------------------------------------------------------------
    # accounts
    # ------------------------------------------------------------------
    def _load_accounts(self) -> None:
        accounts = MicrosoftAuth(self.settings.microsoft_client_id, self.store.accounts_file).load_accounts()
        if accounts:
            self.session = accounts[0]
            self.account_label.configure(text=f"已登录：{self.session.username}")

    def _switch_account(self) -> None:
        accounts = MicrosoftAuth(self.settings.microsoft_client_id, self.store.accounts_file).load_accounts()
        if not accounts:
            self._login()
            return
        if len(accounts) == 1:
            self._login()
            return
        current = next((i for i, a in enumerate(accounts) if self.session and a.uuid == self.session.uuid), 0)
        self.session = accounts[(current + 1) % len(accounts)]
        self.account_label.configure(text=f"已登录：{self.session.username}")

    def _login(self) -> None:
        auth = MicrosoftAuth(self.settings.microsoft_client_id, self.store.accounts_file)
        self._status("正在获取设备码…")

        def start() -> DeviceCode:
            return auth.start_device_code()

        def ready(device: DeviceCode) -> None:
            webbrowser.open(device.verification_uri)
            dialog = tk.Toplevel(self)
            dialog.title("Microsoft 登录")
            dialog.geometry("420x200")
            ttk.Label(dialog, text="在浏览器中打开下面的地址，并输入代码：", padding=10).pack()
            ttk.Label(dialog, text=device.verification_uri, foreground="blue").pack()
            ttk.Label(dialog, text=device.user_code, font=("Consolas", 22, "bold")).pack(pady=10)
            ttk.Label(dialog, text="登录完成后此窗口会自动关闭").pack()

            def poll() -> None:
                def work() -> AuthSession:
                    return auth.wait_for_login(device)

                def done(session: AuthSession) -> None:
                    self.session = session
                    self.account_label.configure(text=f"已登录：{session.username}")
                    self._status(f"已登录 {session.username}")
                    dialog.destroy()

                def failed(exc: Exception) -> None:
                    dialog.destroy()
                    self._show_error(exc)

                self._async(work, done, failed)

            threading.Thread(target=poll, daemon=True).start()

        self._async(start, ready)

    # ------------------------------------------------------------------
    # mods
    # ------------------------------------------------------------------
    def _search_mods(self) -> None:
        query = self.mod_query.get().strip()
        if not query:
            return
        source = self.mod_source.get()
        game_version = self.mod_game_version.get().strip() or None
        loader = self.mod_loader.get().strip() or None
        self._status(f"搜索 {source}…")

        def work() -> list:
            if source == "Modrinth":
                return ModrinthClient().search(query, game_version=game_version, loader=loader)
            return CurseForgeClient(self.settings.curseforge_key).search(
                query, game_version=game_version, loader=loader
            )

        def done(hits: list) -> None:
            self.mod_hits = hits
            self.mod_results.delete(*self.mod_results.get_children())
            for hit in hits:
                self.mod_results.insert("", "end", values=(hit.name, hit.downloads))
            self._status(f"{len(hits)} 个结果")

        self._async(work, done)

    def _mod_selected(self) -> None:
        selection = self.mod_results.selection()
        if not selection:
            return
        index = self.mod_results.index(selection[0])
        if 0 <= index < len(self.mod_hits):
            self._status(f"{self.mod_hits[index].name}：{self.mod_hits[index].description[:120]}")

    def _import_modpack(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 Modrinth 整合包",
            filetypes=[("Modrinth 整合包", "*.mrpack"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.busy = True
        self._status("导入整合包…")

        def work() -> Path:
            java = pick_java(self.javas or find_javas(self.settings.java_path), None)
            return install_mrpack(
                Path(path),
                Path(self.settings.game_dir) / "instances",
                mirror=self.settings.mirror,
                java=java,
            )

        def done(dest: Path) -> None:
            self.busy = False
            self._refresh_installed()
            self._status(f"整合包已安装：{dest.name}")
            messagebox.showinfo("MCLite", f"整合包已安装到：\n{dest}")

        self._async(work, done)

    def _import_cfpack(self) -> None:
        if not self.settings.curseforge_key:
            messagebox.showinfo("MCLite", "CurseForge 整合包需要 API Key，请先在设置里填写。")
            return
        path = filedialog.askopenfilename(
            title="选择 CurseForge 整合包",
            filetypes=[("CurseForge 整合包", "*.zip"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.busy = True
        self._status("导入 CurseForge 整合包…")

        def work() -> Path:
            java = pick_java(self.javas or find_javas(self.settings.java_path), None)
            return install_cfpack(
                Path(path),
                Path(self.settings.game_dir) / "instances",
                api_key=self.settings.curseforge_key,
                mirror=self.settings.mirror,
                java=java,
            )

        def done(dest: Path) -> None:
            self.busy = False
            self._refresh_installed()
            self._status(f"整合包已安装：{dest.name}")
            messagebox.showinfo("MCLite", f"整合包已安装到：\n{dest}")

        self._async(work, done)

    def _install_mod(self) -> None:
        selection = self.mod_results.selection()
        if not selection:
            return
        hit = self.mod_hits[self.mod_results.index(selection[0])]
        game_version = self.mod_game_version.get().strip() or None
        loader = self.mod_loader.get().strip() or None
        mods_dir = Path(self.settings.game_dir) / "mods"
        self._status(f"下载 {hit.name}…")

        def work() -> list[Path]:
            if hit.source == "modrinth":
                return ModrinthClient().install(hit.id, mods_dir, game_version=game_version, loader=loader)
            return CurseForgeClient(self.settings.curseforge_key).install(
                hit.id, mods_dir, game_version=game_version, loader=loader
            )

        def done(paths: list[Path]) -> None:
            self._status("已下载：" + "、".join(p.name for p in paths))

        self._async(work, done)


def main() -> None:
    app = LauncherApp()
    app.mainloop()


if __name__ == "__main__":
    main()
