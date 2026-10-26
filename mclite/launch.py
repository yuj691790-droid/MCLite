"""Building the java command line and starting the game."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .java import JavaInfo
from .meta import OsContext, Version, library_downloads

LAUNCHER_NAME = "MCLite"


class LaunchError(Exception):
    pass


@dataclass
class AuthData:
    username: str
    uuid: str
    access_token: str
    user_type: str = "msa"
    xuid: str = ""
    client_token: str = ""


@dataclass
class LaunchSpec:
    command: list[str]
    cwd: Path
    natives_dir: Path
    main_class: str
    classpath: list[str] = field(default_factory=list)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def build_command(
    version: Version,
    game_dir: Path,
    java: JavaInfo,
    auth: AuthData,
    *,
    memory_mb: int = 2048,
    width: int | None = None,
    height: int | None = None,
    extra_jvm_args: str = "",
    server: str | None = None,
    port: int | None = None,
    demo: bool = False,
) -> LaunchSpec:
    game_dir = Path(game_dir)
    ctx = OsContext(
        name="windows" if os.name == "nt" else ("osx" if sys.platform == "darwin" else "linux"),
        arch="x86_64",
        features={
            "is_demo_user": demo,
            "has_custom_resolution": width is not None and height is not None,
            "has_quick_plays_support": False,
            "is_quick_play_singleplayer": False,
            "is_quick_play_multiplayer": False,
            "is_quick_play_realms": False,
        },
    )
    jvm_args, game_args = version.arguments(ctx)

    artifacts, _ = library_downloads(version.libraries(ctx), ctx)
    classpath = [str(game_dir / "libraries" / path) for path, _ in artifacts]
    classpath.append(str(game_dir / "versions" / version.jar_id / f"{version.jar_id}.jar"))
    classpath = _dedupe(classpath)

    natives_dir = game_dir / "versions" / version.id / "natives"
    natives_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = game_dir / "assets"
    index_id = version.asset_index.get("id", version.assets)
    game_assets = assets_dir / "virtual" / index_id
    if not game_assets.is_dir():
        game_assets = assets_dir

    uuid_plain = auth.uuid.replace("-", "")
    subs = {
        "${auth_player_name}": auth.username,
        "${auth_uuid}": uuid_plain,
        "${auth_access_token}": auth.access_token,
        "${auth_session}": f"token:{auth.access_token}:{uuid_plain}",
        "${client_token}": auth.client_token or uuid_plain,
        "${clientid}": auth.client_token or uuid_plain,
        "${auth_xuid}": auth.xuid or uuid_plain,
        "${user_type}": auth.user_type,
        "${user_properties}": "{}",
        "${version_name}": version.id,
        "${version_type}": version.type,
        "${game_directory}": str(game_dir),
        "${game_assets}": str(game_assets),
        "${assets_root}": str(assets_dir),
        "${assets_index_name}": index_id,
        "${natives_directory}": str(natives_dir),
        "${classpath}": os.pathsep.join(classpath),
        "${classpath_separator}": os.pathsep,
        "${library_directory}": str(game_dir / "libraries"),
        "${launcher_name}": LAUNCHER_NAME,
        "${launcher_version}": __version__,
        "${resolution_width}": str(width or 854),
        "${resolution_height}": str(height or 480),
    }

    if not jvm_args:
        jvm_args = [
            f"-Djava.library.path={natives_dir}",
            "-cp",
            "${classpath}",
        ]

    command: list[str] = [java.path]
    if not any(arg.startswith("-Xmx") for arg in jvm_args):
        command.append(f"-Xmx{max(512, int(memory_mb))}m")
        command.append(f"-Xms{min(max(512, int(memory_mb)), 1024)}m")
    command.extend(_substitute(jvm_args, subs))
    # logging.client.argument 不属于 arguments.jvm，需要单独加入主类之前。
    logging = (version.raw.get("logging") or {}).get("client") or {}
    log_file = logging.get("file") or {}
    log_path = assets_dir / "log_configs" / log_file.get("id", "client.xml")
    if logging.get("argument") and log_path.is_file():
        command.append(logging["argument"].replace("${path}", str(log_path)))
    for raw in extra_jvm_args.replace(",", " ").split():
        command.append(raw)
    command.append(version.main_class)
    command.extend(_substitute(game_args, subs))

    if width and height and not any(a == "--width" for a in command):
        command += ["--width", str(width), "--height", str(height)]
    if server:
        command += ["--server", server]
        if port:
            command += ["--port", str(port)]

    return LaunchSpec(
        command=command,
        cwd=game_dir,
        natives_dir=natives_dir,
        main_class=version.main_class,
        classpath=classpath,
    )


def _substitute(args: list[str], subs: dict[str, str]) -> list[str]:
    out: list[str] = []
    for arg in args:
        for key, value in subs.items():
            if key in arg:
                arg = arg.replace(key, value)
        out.append(arg)
    return out


def launch(spec: LaunchSpec, log_path: Path | None = None) -> subprocess.Popen:
    if not Path(spec.command[0]).is_file():
        raise LaunchError(f"找不到 Java：{spec.command[0]}")
    missing = [path for path in spec.classpath if not Path(path).is_file()]
    if missing:
        preview = "\n".join(missing[:5])
        raise LaunchError(f"版本文件不完整，缺少 {len(missing)} 个库：\n{preview}\n请在“下载”页重新安装该版本。")
    out = subprocess.DEVNULL
    handle = None
    if log_path is not None:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "wb")
        out = handle
    try:
        process = subprocess.Popen(
            spec.command,
            cwd=str(spec.cwd),
            stdout=out,
            stderr=subprocess.STDOUT if handle is not None else subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0,
        )
    except OSError as exc:
        if handle is not None:
            handle.close()
        raise LaunchError(f"无法启动游戏：{exc}") from exc
    if handle is not None:
        # The child inherited the descriptor; the parent copy can close.
        handle.close()
    return process
