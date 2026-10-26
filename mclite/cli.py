"""Headless entry point: install and inspect versions without the Tk UI."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .auth import MicrosoftAuth, offline_uuid
from .config import ConfigStore
from .installer import Installer
from .javaruntime import install_java
from .java import find_javas, pick_java, required_java_major
from .launch import AuthData, build_command, launch as launch_game
from .modpacks import install_cfpack, install_mrpack
from .loaders import (
    fabric_versions,
    forge_versions,
    install_fabric,
    install_forge,
    install_neoforge,
    neoforge_versions,
)
from .meta import Manifest, Version


def _progress(phase: str, done: int, total: int) -> None:
    if total:
        percent = done * 100 // total
        sys.stderr.write(f"\r{phase:10s} {percent:3d}% ({done}/{total})")
    else:
        sys.stderr.write(f"\r{phase}")
    sys.stderr.flush()


def cmd_list(args: argparse.Namespace, store: ConfigStore) -> int:
    manifest = Manifest.fetch(mirror=store.settings.mirror)
    if args.loaders:
        print("latest release:", manifest.latest_release)
        return 0
    for version in manifest.versions[: args.limit]:
        print(f"{version.id:20s} {version.type}")
    return 0


def cmd_install(args: argparse.Namespace, store: ConfigStore) -> int:
    game_dir = Path(store.settings.game_dir)
    mirror = store.settings.mirror
    version_id = args.version
    if args.loader == "vanilla":
        Installer(game_dir, mirror=mirror).install(version_id, _progress)
        print(f"\ninstalled {version_id}")
        return 0

    loader = args.loader_version
    if not loader:
        choices = (
            fabric_versions(version_id, mirror=mirror)
            if args.loader == "fabric"
            else forge_versions(version_id, mirror=mirror)
            if args.loader == "forge"
            else neoforge_versions(version_id)
        )
        if not choices:
            print(f"no {args.loader} build for {version_id}", file=sys.stderr)
            return 1
        loader = choices[0]
        print(f"using {args.loader} {loader}")

    if args.loader == "fabric":
        new_id = install_fabric(version_id, loader, game_dir, mirror=mirror, progress=_progress)
    else:
        version = Version.load(version_id, game_dir, mirror=mirror)
        java = pick_java(find_javas(store.settings.java_path), required_java_major(version_id, version.raw))
        if java is None:
            print("no Java found", file=sys.stderr)
            return 1
        if args.loader == "forge":
            new_id = install_forge(version_id, loader, game_dir, java, mirror=mirror, progress=_progress)
        else:
            new_id = install_neoforge(version_id, loader, game_dir, java, progress=_progress)
    print(f"\ninstalled {new_id}")
    return 0


def cmd_cfpack(args: argparse.Namespace, store: ConfigStore) -> int:
    if not store.settings.curseforge_key:
        print("CurseForge 需要 API Key（设置里的 curseforge_key）", file=sys.stderr)
        return 2
    instances = Path(store.settings.game_dir) / "instances"
    java = pick_java(find_javas(store.settings.java_path), None)
    dest = install_cfpack(
        Path(args.file),
        instances,
        api_key=store.settings.curseforge_key,
        instance_name=args.name or None,
        mirror=store.settings.mirror,
        java=java,
        progress=_progress,
    )
    print(f"\ninstalled modpack into {dest}")
    return 0


def cmd_launch(args: argparse.Namespace, store: ConfigStore) -> int:
    game_dir = Path(store.settings.game_dir)
    if args.offline:
        auth = AuthData(args.offline, offline_uuid(args.offline), "0", user_type="legacy")
    else:
        accounts = MicrosoftAuth(store.settings.microsoft_client_id, store.accounts_file).load_accounts()
        if not accounts:
            print("没有已登录账号；可以先用 --offline 名字 测试启动，或在 GUI 里登录。", file=sys.stderr)
            return 2
        session = accounts[0]
        if session.expired:
            client = MicrosoftAuth(store.settings.microsoft_client_id, store.accounts_file)
            session = client.refresh(session)
        auth = AuthData(session.username, session.uuid, session.access_token, xuid=session.xuid)

    version = Version.load(args.version, game_dir, mirror=store.settings.mirror)
    java = pick_java(find_javas(store.settings.java_path), required_java_major(args.version, version.raw))
    if java is None:
        print("没有可用 Java；先运行 `python -m mclite java --major 21`。", file=sys.stderr)
        return 2
    spec = build_command(
        version,
        game_dir,
        java,
        auth,
        memory_mb=store.settings.memory_mb,
        extra_jvm_args=store.settings.extra_jvm_args,
    )
    if args.dry_run:
        import subprocess as _subprocess

        print(_subprocess.list2cmdline(spec.command) if os.name == "nt" else " ".join(spec.command))
        return 0
    log_path = store.base_dir / "logs" / f"game-{args.version}-{int(time.time())}.log"
    process = launch_game(spec, log_path)
    print(f"pid={process.pid} log={log_path}")
    if args.wait:
        print(f"exit={process.wait()}")
    return 0


def cmd_doctor(args: argparse.Namespace, store: ConfigStore) -> int:
    from . import __version__
    from .meta import Manifest

    ok = True
    print(f"MCLite {__version__}")
    print(f"base dir : {store.base_dir}")
    print(f"game dir : {store.game_dir}")
    try:
        store.game_dir.mkdir(parents=True, exist_ok=True)
        probe = store.game_dir / ".mclite-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print("[OK]   游戏目录可写")
    except OSError as exc:
        ok = False
        print(f"[FAIL] 游戏目录不可写：{exc}")

    javas = find_javas(store.settings.java_path)
    if javas:
        print(f"[OK]   Java：{javas[0].label}" + (f"（共 {len(javas)} 个）" if len(javas) > 1 else ""))
    else:
        print("[WARN] 没有检测到 Java，可用 `python -m mclite java --major 21` 安装")

    try:
        manifest = Manifest.fetch(mirror=store.settings.mirror)
        print(f"[OK]   版本清单：最新 {manifest.latest_release}，共 {len(manifest.versions)} 个版本（镜像={'开' if store.settings.mirror else '关'}）")
    except Exception as exc:  # noqa: BLE001
        ok = False
        print(f"[FAIL] 无法获取版本清单：{exc}")

    versions_dir = store.game_dir / "versions"
    installed = [p.name for p in versions_dir.iterdir() if (p / f"{p.name}.json").is_file()] if versions_dir.is_dir() else []
    print(f"[OK]   已安装版本：{len(installed)} 个" + (f"（{', '.join(installed[:5])}）" if installed else ""))
    print(f"[INFO] 日志目录：{store.base_dir / 'logs'}")
    return 0 if ok else 1


def cmd_java(args: argparse.Namespace, store: ConfigStore) -> int:
    path = install_java(args.major, store.base_dir, progress=_progress)
    print(f"\nJava {args.major} installed: {path}")
    return 0


def cmd_modpack(args: argparse.Namespace, store: ConfigStore) -> int:
    instances = Path(store.settings.game_dir) / "instances"
    java = pick_java(find_javas(store.settings.java_path), None)
    dest = install_mrpack(
        Path(args.file),
        instances,
        instance_name=args.name or None,
        mirror=store.settings.mirror,
        java=java,
        progress=_progress,
    )
    print(f"\ninstalled modpack into {dest}")
    return 0


def cmd_info(args: argparse.Namespace, store: ConfigStore) -> int:
    version = Version.load(args.version, Path(store.settings.game_dir), mirror=store.settings.mirror)
    print("id:", version.id)
    print("main class:", version.main_class)
    print("jar:", version.jar_id)
    print("inherits:", version.raw.get("inheritsFrom", "-"))
    print("libraries:", len(version.raw.get("libraries", [])))
    javas = find_javas(store.settings.java_path)
    print("java:", [j.label for j in javas])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mclite")
    parser.add_argument("--game-dir", help="override the game directory")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list versions from the manifest")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--loaders", action="store_true", help="print only the latest release")

    p_install = sub.add_parser("install", help="install a version")
    p_install.add_argument("version")
    p_install.add_argument("--loader", choices=["vanilla", "fabric", "forge", "neoforge"], default="vanilla")
    p_install.add_argument("--loader-version", default="")

    p_info = sub.add_parser("info", help="show a version's metadata")
    p_info.add_argument("version")

    sub.add_parser("doctor", help="check environment, network and directories")

    p_launch = sub.add_parser("launch", help="launch a version from the command line")
    p_launch.add_argument("version")
    p_launch.add_argument("--offline", metavar="NAME", help="use an offline profile instead of a Microsoft account")
    p_launch.add_argument("--dry-run", action="store_true", help="print the java command without launching")
    p_launch.add_argument("--wait", action="store_true", help="wait for the game to exit")

    p_java = sub.add_parser("java", help="download a JRE from Adoptium")
    p_java.add_argument("--major", type=int, default=21)

    p_pack = sub.add_parser("modpack", help="install a Modrinth .mrpack file")
    p_pack.add_argument("file")
    p_pack.add_argument("--name", default="")

    p_cf = sub.add_parser("cfpack", help="install a CurseForge modpack zip (needs API key)")
    p_cf.add_argument("file")
    p_cf.add_argument("--name", default="")

    args = parser.parse_args(argv)
    store = ConfigStore()
    if getattr(args, "game_dir", None):
        store.settings.game_dir = args.game_dir
    handler = {
        "list": cmd_list,
        "install": cmd_install,
        "info": cmd_info,
        "modpack": cmd_modpack,
        "cfpack": cmd_cfpack,
        "doctor": cmd_doctor,
        "launch": cmd_launch,
        "java": cmd_java,
    }[args.command]
    return handler(args, store)


if __name__ == "__main__":
    raise SystemExit(main())
