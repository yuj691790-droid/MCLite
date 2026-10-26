"""Locating and identifying Java runtimes (Windows first, portable otherwise)."""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_VERSION_RE = re.compile(r'version "([^"]+)"')
_LEGACY_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:_(\d+))?")


@dataclass(frozen=True)
class JavaInfo:
    path: str
    version: str
    major: int
    arch: str = ""

    @property
    def label(self) -> str:
        return f"Java {self.version} ({self.arch or '?'}) - {self.path}"


def _java_exe(path: Path) -> Path | None:
    exe = path / "bin" / ("java.exe" if os.name == "nt" else "java")
    return exe if exe.is_file() else None


def _probe(path: str) -> JavaInfo | None:
    try:
        proc = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (proc.stderr or "") + (proc.stdout or "")
    match = _VERSION_RE.search(text)
    if not match:
        return None
    raw = match.group(1)
    legacy = _LEGACY_RE.match(raw)
    if not legacy:
        return None
    major = int(legacy.group(1))
    if major == 1:
        major = int(legacy.group(2))
    arch = ""
    if "64-Bit" in text or "x86_64" in text or "aarch64" in text:
        arch = "x86_64" if "aarch64" not in text else "arm64"
    return JavaInfo(path=path, version=raw, major=major, arch=arch)


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        dirs.append(Path(java_home))
    if os.name == "nt":
        roots = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Java",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Eclipse Adoptium",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Amazon Corretto",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Zulu",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Java",
        ]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots += [
                Path(local) / "Programs" / "Eclipse Adoptium",
                Path(local) / "Programs" / "Microsoft",
            ]
        for root in roots:
            if root.is_dir():
                dirs.extend(Path(p) for p in glob.glob(str(root / "*")) if Path(p).is_dir())
    else:
        for pattern in ("/usr/lib/jvm/*", str(Path.home() / ".jdks" / "*")):
            dirs.extend(Path(p) for p in glob.glob(pattern) if Path(p).is_dir())
    return dirs


def find_javas(extra: str | Iterable[str] | None = None) -> list[JavaInfo]:
    """Probe every plausible java and return unique matches, newest first."""
    candidates: list[str] = []
    if extra:
        candidates.extend([extra] if isinstance(extra, str) else list(extra))
    on_path = shutil.which("java")
    if on_path:
        candidates.append(on_path)
    for directory in _candidate_dirs():
        exe = _java_exe(directory)
        if exe:
            candidates.append(str(exe))

    seen: set[str] = set()
    found: list[JavaInfo] = []
    for candidate in candidates:
        resolved = os.path.abspath(candidate)
        if resolved in seen or not os.path.isfile(resolved):
            continue
        seen.add(resolved)
        info = _probe(resolved)
        if info:
            found.append(info)
    found.sort(key=lambda j: (j.major, j.version), reverse=True)
    return found


def pick_java(javas: list[JavaInfo], required_major: int | None) -> JavaInfo | None:
    """Prefer an exact major match, then anything newer, then anything."""
    if not javas:
        return None
    if required_major is None:
        return javas[0]
    exact = [j for j in javas if j.major == required_major]
    if exact:
        return exact[0]
    newer = [j for j in javas if j.major > required_major]
    if newer:
        return min(newer, key=lambda j: j.major)
    return javas[0]


def required_java_major(version_id: str, metadata: dict | None = None) -> int | None:
    """优先读取版本元数据；旧版缺少 javaVersion 时再按版本号推断。"""
    if metadata and (metadata.get("javaVersion") or {}).get("majorVersion"):
        return int(metadata["javaVersion"]["majorVersion"])
    release = version_id.split("-")[0]
    parts = release.split(".")
    try:
        year, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return None
    sequence = (year, minor, int(parts[2]) if len(parts) > 2 else 0)
    if sequence >= (1, 20, 5):
        return 21
    if sequence >= (1, 18):
        return 17
    if sequence >= (1, 17):
        return 16
    if sequence >= (1, 13):
        return 8
    return 8
