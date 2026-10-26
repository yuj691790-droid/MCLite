"""Downloading a JRE from Adoptium (Eclipse Temurin).

Minecraft needs a matching Java; most users do not have one installed. The
launcher can fetch a JRE zip, verify its SHA-256 and unpack it under
``<base>/java``.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .net import Downloader, DownloadTask, fetch_json

ADOPTIUM = "https://api.adoptium.net/v3/assets/latest/{major}/hotspot"

Progress = Callable[[str, int, int], None]


class JavaRuntimeError(Exception):
    pass


@dataclass
class JavaRelease:
    major: int
    version: str
    url: str
    filename: str
    size: int | None = None
    sha256: str | None = None


def platform_tags() -> tuple[str, str]:
    """Map this OS/CPU to Adoptium's ``os``/``architecture`` values."""
    machine = platform.machine().lower()
    arch = "aarch64" if machine in ("arm64", "aarch64") else "x64"
    if os.name == "nt":
        return "windows", arch
    if sys.platform == "darwin":
        return "mac", arch
    return "linux", arch


def fetch_java_release(
    major: int,
    *,
    os_name: str | None = None,
    arch: str | None = None,
    image_type: str = "jre",
    vendor: str = "eclipse",
) -> JavaRelease:
    target_os, target_arch = platform_tags()
    params = urllib.parse.urlencode(
        {
            "architecture": arch or target_arch,
            "image_type": image_type,
            "os": os_name or target_os,
            "vendor": vendor,
            "heap_size": "normal",
        }
    )
    data = fetch_json(f"{ADOPTIUM.format(major=major)}?{params}")
    if not isinstance(data, list) or not data:
        raise JavaRuntimeError(f"Adoptium 没有 Java {major} 的可用发行版")
    asset = data[0]
    package = ((asset.get("binary") or {}).get("package") or {})
    link = package.get("link")
    if not link:
        raise JavaRuntimeError("Adoptium 返回的发行版缺少下载地址")
    version = (asset.get("version") or {}).get("semver") or str(major)
    return JavaRelease(
        major=int((asset.get("version") or {}).get("major", major)),
        version=version,
        url=link,
        filename=package.get("name") or Path(urllib.parse.urlparse(link).path).name,
        size=package.get("size"),
        sha256=package.get("checksum"),
    )


def _extract_zip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            relative = info.filename.replace("\\", "/")
            out = (target / relative).resolve()
            if out != root and root not in out.parents:
                raise JavaRuntimeError(f"Java 压缩包包含越界路径：{info.filename}")
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as source, open(out, "wb") as output:
                shutil.copyfileobj(source, output)


def install_java(
    major: int,
    base_dir: Path,
    *,
    downloader: Downloader | None = None,
    progress: Progress | None = None,
) -> Path:
    """Install a JRE and return the path to its java executable."""
    release = fetch_java_release(major)
    base = Path(base_dir)
    cache = base / "cache" / "java"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / release.filename
    task = DownloadTask(url=release.url, dest=archive, sha256=release.sha256, size=release.size)
    total = release.size or 0
    if progress:
        progress("java", 0, total)
    written = 0

    def advance(count: int) -> None:
        nonlocal written
        written += count
        if progress:
            progress("java", min(written, total or written), total or written)

    (downloader or Downloader()).ensure(task, advance)

    safe_version = release.version.replace("+", "_")
    java_root = base / "java"
    java_root.mkdir(parents=True, exist_ok=True)
    target = java_root / f"jre-{major}-{safe_version}"
    staging = java_root / f".jre-{major}-{safe_version}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    _extract_zip(archive, staging)
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    for candidate in target.rglob("bin/java.exe" if os.name == "nt" else "bin/java"):
        if candidate.is_file():
            return candidate
    raise JavaRuntimeError("Java 解压完成但找不到 java 可执行文件")
