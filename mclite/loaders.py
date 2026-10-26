"""Mod loader installation: Fabric (profile JSON) and Forge/NeoForge (installer)."""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable

from .installer import Installer
from .java import JavaInfo
from .net import BMCLAPI, Downloader, DownloadTask, fetch_bytes, fetch_json

FABRIC_META = "https://meta.fabricmc.net/v2"
FABRIC_MIRROR = f"{BMCLAPI}/fabric-meta/v2"
FORGE_MAVEN = "https://maven.minecraftforge.net/net/minecraftforge/forge"
NEOFORGE_MAVEN = "https://maven.neoforged.net/releases/net/neoforged/neoforge"
NEOFORGE_VERSIONS = "https://maven.neoforged.net/api/maven/versions/releases/net/neoforged/neoforge"

Progress = Callable[[str, int, int], None]


class LoaderError(Exception):
    pass


def _sort_versions(versions: list[str]) -> list[str]:
    """Newest first, comparing numeric dotted versions; junk sorts last."""

    def key(value: str) -> tuple:
        parts: list = []
        for piece in value.replace("-", ".").split("."):
            parts.append(int(piece) if piece.isdigit() else piece)
        return tuple(parts)

    try:
        return sorted(versions, key=key, reverse=True)
    except TypeError:
        return sorted(versions, reverse=True)


# ---------------------------------------------------------------------------
# Fabric
# ---------------------------------------------------------------------------
def fabric_versions(mc_version: str, *, mirror: bool = True) -> list[str]:
    base = FABRIC_MIRROR if mirror else FABRIC_META
    data = fetch_json(f"{base}/versions/loader/{mc_version}")
    if not isinstance(data, list):
        raise LoaderError(f"Fabric 没有适用于 {mc_version} 的版本")
    return [item["loader"]["version"] for item in data if item.get("loader")]


def install_fabric(
    mc_version: str,
    loader_version: str,
    game_dir: Path,
    *,
    mirror: bool = True,
    downloader: Downloader | None = None,
    progress: Progress | None = None,
) -> str:
    base = FABRIC_MIRROR if mirror else FABRIC_META
    profile = fetch_json(f"{base}/versions/loader/{mc_version}/{loader_version}/profile/json")
    version_id = profile.get("id") or f"fabric-loader-{loader_version}-{mc_version}"
    target = Path(game_dir) / "versions" / version_id
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{version_id}.json").write_text(
        json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    Installer(game_dir, mirror=mirror, downloader=downloader).install(version_id, progress)
    return version_id


# ---------------------------------------------------------------------------
# Forge
# ---------------------------------------------------------------------------
def forge_versions(mc_version: str, *, mirror: bool = True) -> list[str]:
    """Bare Forge build numbers (``47.4.5``), newest first."""
    if mirror:
        data = fetch_json(f"{BMCLAPI}/forge/minecraft/{mc_version}")
        if not isinstance(data, list):
            raise LoaderError("BMCLAPI 返回了意外的 Forge 版本格式")
        versions = [
            item.get("version") if isinstance(item, dict) else item
            for item in data
        ]
        return _sort_versions([v for v in versions if isinstance(v, str) and v])
    xml = fetch_bytes(f"{FORGE_MAVEN}/maven-metadata.xml").decode("utf-8", "replace")
    root = ET.fromstring(xml)
    prefix = f"{mc_version}-"
    versions = [
        node.text[len(prefix) :]
        for node in root.iter("version")
        if node.text and node.text.startswith(prefix)
    ]
    return _sort_versions(versions)


def install_forge(
    mc_version: str,
    forge_version: str,
    game_dir: Path,
    java: JavaInfo,
    *,
    mirror: bool = True,
    downloader: Downloader | None = None,
    progress: Progress | None = None,
) -> str:
    game_dir = Path(game_dir)
    full = f"{mc_version}-{forge_version}"
    path = f"net/minecraftforge/forge/{full}/forge-{full}-installer.jar"
    official = f"{FORGE_MAVEN}/{full}/forge-{full}-installer.jar"
    url = f"{BMCLAPI}/maven/{path}" if mirror else official
    installer_jar = game_dir / ".mclite" / "installers" / f"forge-{full}-installer.jar"
    Downloader().ensure(DownloadTask(url=url, dest=installer_jar, fallback=(official,)))
    # The upstream installer patches an existing vanilla version.
    Installer(game_dir, mirror=mirror, downloader=downloader).install(mc_version, progress)
    _ensure_launcher_profiles(game_dir)
    _run_installer(java, installer_jar, game_dir, progress)
    version_id = _find_generated_version(game_dir, mc_version, "forge")
    # Complete missing libraries and extract natives into the new version dir.
    Installer(game_dir, mirror=mirror, downloader=downloader).install(version_id, progress)
    return version_id


# ---------------------------------------------------------------------------
# NeoForge
# ---------------------------------------------------------------------------
def neoforge_versions(mc_version: str) -> list[str]:
    """NeoForge builds for this MC version, newest first."""
    data = fetch_json(NEOFORGE_VERSIONS)
    versions = data.get("versions") or []
    parts = mc_version.split(".")
    if len(parts) < 2 or parts[0] != "1":
        return []
    minor = parts[1]
    patch = parts[2] if len(parts) > 2 else "0"
    prefix = f"{minor}.{patch}."
    matched = [v for v in versions if isinstance(v, str) and v.startswith(prefix)]
    return _sort_versions(matched)


def install_neoforge(
    mc_version: str,
    neoforge_version: str,
    game_dir: Path,
    java: JavaInfo,
    *,
    mirror: bool = True,
    downloader: Downloader | None = None,
    progress: Progress | None = None,
) -> str:
    game_dir = Path(game_dir)
    jar_name = f"neoforge-{neoforge_version}-installer.jar"
    official = f"{NEOFORGE_MAVEN}/{neoforge_version}/{jar_name}"
    mirrored = f"{BMCLAPI}/maven/net/neoforged/neoforge/{neoforge_version}/{jar_name}"
    url = mirrored if mirror else official
    installer_jar = game_dir / ".mclite" / "installers" / jar_name
    Downloader().ensure(DownloadTask(url=url, dest=installer_jar, fallback=(official,)))
    # The upstream installer patches an existing vanilla version.
    Installer(game_dir, mirror=mirror, downloader=downloader).install(mc_version, progress)
    _ensure_launcher_profiles(game_dir)
    _run_installer(java, installer_jar, game_dir, progress)
    version_id = _find_generated_version(game_dir, mc_version, "neoforge")
    Installer(game_dir, mirror=mirror, downloader=downloader).install(version_id, progress)
    return version_id


# ---------------------------------------------------------------------------
# Shared installer helpers
# ---------------------------------------------------------------------------
def _ensure_launcher_profiles(game_dir: Path) -> None:
    path = game_dir / "launcher_profiles.json"
    if not path.is_file():
        path.write_text(
            json.dumps({"profiles": {}, "settings": {}, "version": 3}, indent=2),
            encoding="utf-8",
        )


def _run_installer(java: JavaInfo, jar: Path, game_dir: Path, progress: Progress | None) -> None:
    if progress:
        progress("installer", 0, 1)
    # Some JDKs (Termux's, and locked-down Windows profiles) point
    # java.io.tmpdir at a path that does not exist; give the installer ours.
    tmp_dir = game_dir / ".mclite" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    command = [
        java.path,
        f"-Djava.io.tmpdir={tmp_dir}",
        "-jar",
        str(jar),
        "--installClient",
        str(game_dir),
    ]
    try:
        proc = subprocess.run(
            command,
            cwd=str(game_dir),
            capture_output=True,
            text=True,
            timeout=1200,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LoaderError(f"无法运行安装器：{exc}") from exc
    if proc.returncode != 0:
        tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
        raise LoaderError(f"加载器安装器失败（退出码 {proc.returncode}）：\n{tail}")
    if progress:
        progress("installer", 1, 1)


def _find_generated_version(game_dir: Path, mc_version: str, needle: str) -> str:
    versions_dir = game_dir / "versions"
    best: tuple[float, str] | None = None
    for child in versions_dir.iterdir() if versions_dir.is_dir() else []:
        meta = child / f"{child.name}.json"
        if not meta.is_file():
            continue
        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if raw.get("inheritsFrom") != mc_version:
            continue
        if needle not in child.name.lower():
            continue
        mtime = meta.stat().st_mtime
        if best is None or mtime > best[0]:
            best = (mtime, child.name)
    if best is None:
        raise LoaderError(f"安装器没有生成 {needle} 版本，请查看安装日志")
    return best[1]
