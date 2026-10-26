"""Modrinth ``.mrpack`` modpack import.

An ``.mrpack`` is a zip with ``modrinth.index.json`` at the root plus
``overrides/`` (and optionally ``client-overrides/``) trees. Files are
downloaded by URL and verified with SHA-1; overrides are copied verbatim.
"""

from __future__ import annotations

import json
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .installer import Installer
from .java import JavaInfo
from .loaders import install_fabric, install_forge, install_neoforge
from .net import Downloader, DownloadTask

Progress = Callable[[str, int, int], None]


class ModpackError(Exception):
    pass


@dataclass
class ModpackFile:
    path: str
    url: str
    sha1: str | None = None
    size: int | None = None


@dataclass
class ModpackIndex:
    name: str
    version_id: str
    mc_version: str
    loader: str | None = None
    loader_version: str | None = None
    files: list[ModpackFile] = field(default_factory=list)


def _safe_join(base: Path, relative: str) -> Path:
    root = base.resolve()
    target = (base / relative).resolve()
    if target != root and root not in target.parents:
        raise ModpackError(f"整合包包含越界路径：{relative}")
    return target


def parse_mrpack(path: Path) -> ModpackIndex:
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("modrinth.index.json")
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise ModpackError("不是有效的 .mrpack（缺少 modrinth.index.json）") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ModpackError("modrinth.index.json 不是合法 JSON") from exc
    if data.get("game") != "minecraft":
        raise ModpackError(f"不支持的整合包类型：{data.get('game')}")
    deps = data.get("dependencies") or {}
    mc_version = deps.get("minecraft")
    if not mc_version:
        raise ModpackError("整合包没有指定 Minecraft 版本")
    loader = loader_version = None
    for key, name in (("fabric-loader", "fabric"), ("forge", "forge"), ("neoforge", "neoforge")):
        if deps.get(key):
            loader, loader_version = name, str(deps[key])
            break
    files: list[ModpackFile] = []
    for item in data.get("files") or []:
        downloads = item.get("downloads") or []
        if not downloads:
            continue
        files.append(
            ModpackFile(
                path=item["path"],
                url=downloads[0],
                sha1=(item.get("hashes") or {}).get("sha1"),
                size=item.get("fileSize"),
            )
        )
    return ModpackIndex(
        name=data.get("name") or Path(path).stem,
        version_id=data.get("versionId", "1.0.0"),
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        files=files,
    )


def _copy_overrides(archive: zipfile.ZipFile, prefix: str, dest: Path) -> None:
    for info in archive.infolist():
        if not info.filename.startswith(prefix) or info.is_dir():
            continue
        relative = info.filename[len(prefix) :]
        if not relative:
            continue
        target = _safe_join(dest, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, open(target, "wb") as output:
            shutil.copyfileobj(source, output)


def install_mrpack(
    path: Path,
    instances_root: Path,
    *,
    instance_name: str | None = None,
    mirror: bool = True,
    downloader: Downloader | None = None,
    java: JavaInfo | None = None,
    progress: Progress | None = None,
) -> Path:
    """Install a modpack into ``instances_root/<name>`` and return that dir."""
    index = parse_mrpack(path)
    folder = instance_name or f"{index.name} {index.version_id}".strip()
    folder = "".join(ch for ch in folder if ch not in '\\/:*?"<>|').strip() or "modpack"
    dest = Path(instances_root) / folder
    dest.mkdir(parents=True, exist_ok=True)

    downloader = downloader or Downloader()
    tasks = [
        DownloadTask(
            url=item.url,
            dest=_safe_join(dest, item.path),
            sha1=item.sha1,
            size=item.size,
        )
        for item in index.files
    ]
    failures = downloader.ensure_all(tasks, lambda done, total: _report(progress, "files", done, total))
    if failures:
        raise ModpackError("部分文件下载失败：" + "; ".join(str(f) for f in failures[:3]))

    with zipfile.ZipFile(path) as archive:
        _copy_overrides(archive, "overrides/", dest)
        _copy_overrides(archive, "client-overrides/", dest)

    if index.loader and index.loader_version:
        _report(progress, "loader", 0, 1)
        if index.loader == "fabric":
            install_fabric(index.mc_version, index.loader_version, dest, mirror=mirror, downloader=downloader, progress=progress)
        else:
            if java is None:
                raise ModpackError("安装 Forge/NeoForge 整合包需要 Java，请先在设置里指定。")
            if index.loader == "forge":
                install_forge(index.mc_version, index.loader_version, dest, java, mirror=mirror, downloader=downloader, progress=progress)
            else:
                install_neoforge(index.mc_version, index.loader_version, dest, java, mirror=mirror, downloader=downloader, progress=progress)
    else:
        Installer(dest, mirror=mirror, downloader=downloader).install(index.mc_version, progress)
    return dest


def _report(progress: Progress | None, phase: str, done: int, total: int) -> None:
    if progress:
        progress(phase, done, total)


# ---------------------------------------------------------------------------
# CurseForge modpacks (manifest.json + overrides)
# ---------------------------------------------------------------------------
@dataclass
class CFModpackFile:
    project_id: int
    file_id: int
    required: bool = True


@dataclass
class CFModpack:
    name: str
    version: str
    mc_version: str
    loader: str | None
    loader_version: str | None
    files: list[CFModpackFile] = field(default_factory=list)
    overrides: str = "overrides"


def _parse_cf_loader(loader_id: str) -> tuple[str, str]:
    for prefix, name in (("forge-", "forge"), ("neoforge-", "neoforge"), ("fabric-", "fabric")):
        if loader_id.startswith(prefix):
            version = loader_id[len(prefix) :]
            if name == "fabric" and version.startswith("loader-"):
                version = version[len("loader-") :]
            return name, version
    raise ModpackError(f"不支持的 CurseForge 加载器：{loader_id}")


def parse_cfpack(path: Path) -> CFModpack:
    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read("manifest.json")
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise ModpackError("不是有效的 CurseForge 整合包（缺少 manifest.json）") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ModpackError("manifest.json 不是合法 JSON") from exc
    if data.get("manifestType") != "minecraftModpack":
        raise ModpackError("不是 CurseForge Minecraft 整合包")
    minecraft = data.get("minecraft") or {}
    mc_version = minecraft.get("version")
    if not mc_version:
        raise ModpackError("整合包没有指定 Minecraft 版本")
    loader = loader_version = None
    loaders = minecraft.get("modLoaders") or []
    primary = next((item for item in loaders if item.get("primary")), loaders[0] if loaders else None)
    if primary and primary.get("id"):
        loader, loader_version = _parse_cf_loader(primary["id"])
    files = [
        CFModpackFile(
            project_id=int(item["projectID"]),
            file_id=int(item["fileID"]),
            required=bool(item.get("required", True)),
        )
        for item in data.get("files") or []
        if item.get("projectID") and item.get("fileID")
    ]
    return CFModpack(
        name=data.get("name") or Path(path).stem,
        version=str(data.get("version", "1.0.0")),
        mc_version=mc_version,
        loader=loader,
        loader_version=loader_version,
        files=files,
        overrides=data.get("overrides") or "overrides",
    )


def install_cfpack(
    path: Path,
    instances_root: Path,
    *,
    api_key: str,
    client=None,
    instance_name: str | None = None,
    mirror: bool = True,
    downloader: Downloader | None = None,
    java: JavaInfo | None = None,
    progress: Progress | None = None,
) -> Path:
    """Install a CurseForge modpack zip. File URLs are resolved via the API."""
    from .mods import CurseForgeClient

    index = parse_cfpack(path)
    folder = instance_name or f"{index.name} {index.version}".strip()
    folder = "".join(ch for ch in folder if ch not in '\\/:*?"<>|').strip() or "cfpack"
    dest = Path(instances_root) / folder
    dest.mkdir(parents=True, exist_ok=True)

    if client is None:
        client = CurseForgeClient(api_key, downloader)
    downloader = downloader or Downloader()
    tasks = []
    for item in index.files:
        info = client.file_info(item.project_id, item.file_id)
        url = info.get("downloadUrl")
        if not url:
            if item.required:
                raise ModpackError(
                    f"作者禁止第三方下载（project {item.project_id}），请到 CurseForge 网页手动下载。"
                )
            continue
        sha1 = next(
            (h.get("value") for h in (info.get("hashes") or []) if h.get("algo") == 1),
            None,
        )
        tasks.append(
            DownloadTask(
                url=url,
                dest=dest / "mods" / info["fileName"],
                sha1=sha1,
                size=info.get("fileLength"),
            )
        )
    failures = downloader.ensure_all(tasks, lambda done, total: _report(progress, "files", done, total))
    if failures:
        raise ModpackError("部分 Mod 下载失败：" + "; ".join(str(f) for f in failures[:3]))

    with zipfile.ZipFile(path) as archive:
        _copy_overrides(archive, index.overrides.rstrip("/") + "/", dest)

    if index.loader and index.loader_version:
        if index.loader == "fabric":
            install_fabric(index.mc_version, index.loader_version, dest, mirror=mirror, downloader=downloader, progress=progress)
        else:
            if java is None:
                raise ModpackError("安装 Forge/NeoForge 整合包需要 Java，请先在设置里指定。")
            if index.loader == "forge":
                install_forge(index.mc_version, index.loader_version, dest, java, mirror=mirror, downloader=downloader, progress=progress)
            else:
                install_neoforge(index.mc_version, index.loader_version, dest, java, mirror=mirror, downloader=downloader, progress=progress)
    else:
        Installer(dest, mirror=mirror, downloader=downloader).install(index.mc_version, progress)
    return dest
