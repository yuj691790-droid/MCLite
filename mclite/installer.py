"""Installing vanilla versions: client jar, libraries, assets, natives."""

from __future__ import annotations

import fnmatch
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .meta import (
    OsContext,
    Version,
    asset_tasks,
    library_downloads,
    mirror_url,
)
from .net import BMCLAPI, Downloader, DownloadTask

Progress = Callable[[str, int, int], None]


class InstallError(Exception):
    """A required file could not be downloaded."""


@dataclass
class InstallResult:
    version_id: str
    files: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


class Installer:
    def __init__(
        self,
        game_dir: Path,
        *,
        mirror: bool = True,
        downloader: Downloader | None = None,
        os_ctx: OsContext | None = None,
    ) -> None:
        self.game_dir = Path(game_dir)
        self.mirror = mirror
        self.downloader = downloader or Downloader()
        self.ctx = os_ctx or OsContext()

    # -- public ----------------------------------------------------------
    def install(self, version_id: str, progress: Progress | None = None) -> InstallResult:
        result = InstallResult(version_id=version_id)
        version = self._install_chain(version_id, progress, result, set())
        self._extract_natives(version, progress)
        return result

    def _install_chain(
        self,
        version_id: str,
        progress: Progress | None,
        result: InstallResult,
        seen: set[str],
    ) -> Version:
        if version_id in seen:
            raise ValueError(f"circular inheritance at {version_id}")
        seen.add(version_id)

        self._report(progress, "metadata", 0, 1)
        version = Version.load(version_id, self.game_dir, mirror=self.mirror, downloader=self.downloader)
        self._report(progress, "metadata", 1, 1)

        raw = version.raw
        if raw.get("inheritsFrom"):
            self._install_chain(raw["inheritsFrom"], progress, result, seen)

        jar_id = raw.get("jar") or (raw["inheritsFrom"] if raw.get("inheritsFrom") else version_id)
        self._install_client_jar(version, jar_id, progress, result)
        self._install_libraries(version, progress, result)
        self._install_assets(version, progress, result)
        self._install_logging(version, result)
        return version

    # -- pieces ----------------------------------------------------------
    def _install_client_jar(
        self, version: Version, jar_id: str, progress: Progress | None, result: InstallResult
    ) -> None:
        download = version.client_download
        if not download:
            return
        dest = self.game_dir / "versions" / jar_id / f"{jar_id}.jar"
        if self.mirror:
            url = f"{BMCLAPI}/version/{jar_id}/client"
            fallback = (download["url"],)
        else:
            url = download["url"]
            fallback = (f"{BMCLAPI}/version/{jar_id}/client",) if mirror_url(download["url"]) else ()
        task = DownloadTask(
            url=url,
            dest=dest,
            sha1=download.get("sha1"),
            size=download.get("size"),
            fallback=fallback,
        )
        total = download.get("size") or 0
        self._report(progress, "client", 0, total)
        written = 0

        def advance(count: int) -> None:
            nonlocal written
            written += count
            self._report(progress, "client", min(written, total or written), total)

        wrote = self.downloader.ensure(task, advance)
        result.files += 1
        if not wrote:
            result.skipped += 1

    def _install_libraries(self, version: Version, progress: Progress | None, result: InstallResult) -> None:
        artifacts, natives = library_downloads(version.libraries(self.ctx), self.ctx)
        tasks: list[DownloadTask] = []
        for path, artifact in artifacts + natives:
            url = artifact["url"]
            fallback: tuple[str, ...] = ()
            mirrored = mirror_url(url)
            if self.mirror and mirrored:
                url, fallback = mirrored, (artifact["url"],)
            tasks.append(
                DownloadTask(
                    url=url,
                    dest=self.game_dir / "libraries" / path,
                    sha1=artifact.get("sha1"),
                    size=artifact.get("size"),
                    fallback=fallback,
                )
            )
        failures = self.downloader.ensure_all(tasks, lambda done, total: self._report(progress, "libraries", done, total))
        result.files += len(tasks) - len(failures)
        if failures:
            raise InstallError(
                f"{len(failures)} 个库文件下载失败（已保留 .part 以便重试）：" + "; ".join(str(f) for f in failures[:3])
            )

    def _install_assets(self, version: Version, progress: Progress | None, result: InstallResult) -> None:
        index_meta = version.asset_index
        index_id = index_meta.get("id")
        if not index_id:
            return
        index_path = self.game_dir / "assets" / "indexes" / f"{index_id}.json"
        if not index_path.is_file():
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_bytes(fetch_json_bytes(index_meta["url"]))
        index = json.loads(index_path.read_text(encoding="utf-8"))
        tasks = asset_tasks(index, self.game_dir, self.ctx, mirror=self.mirror)
        failures = self.downloader.ensure_all(tasks, lambda done, total: self._report(progress, "assets", done, total))
        result.files += len(tasks) - len(failures)
        if failures:
            raise InstallError(
                f"{len(failures)} 个资源文件下载失败（已保留已下载部分）：" + "; ".join(str(f) for f in failures[:3])
            )
        if index.get("virtual") or index.get("map_to_resources"):
            self._materialize_virtual_assets(index, index_id)

    def _install_logging(self, version: Version, result: InstallResult) -> None:
        logging = (version.raw.get("logging") or {}).get("client") or {}
        file_meta = logging.get("file") or {}
        if not file_meta.get("url"):
            return
        name = file_meta.get("id", "client.xml")
        dest = self.game_dir / "assets" / "log_configs" / name
        url = file_meta["url"]
        mirrored = mirror_url(url)
        task = DownloadTask(
            url=mirrored if (self.mirror and mirrored) else url,
            dest=dest,
            sha1=file_meta.get("sha1"),
            size=file_meta.get("size"),
            fallback=(url,) if mirrored else (),
        )
        try:
            self.downloader.ensure(task)
            result.files += 1
        except Exception as exc:  # noqa: BLE001 - logging config is optional
            result.warnings.append(f"logging config: {exc}")

    def _materialize_virtual_assets(self, index: dict, index_id: str) -> None:
        objects = self.game_dir / "assets" / "objects"
        if index.get("map_to_resources"):
            target = self.game_dir / "resources"
        else:
            target = self.game_dir / "assets" / "virtual" / index_id
        for name, obj in (index.get("objects") or {}).items():
            digest = obj.get("hash")
            if not digest:
                continue
            source = objects / digest[:2] / digest
            dest = target / name
            if source.is_file() and not dest.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(source.read_bytes())

    def _extract_natives(self, version: Version, progress: Progress | None = None) -> None:
        _, natives = library_downloads(version.libraries(self.ctx), self.ctx)
        target = self.game_dir / "versions" / version.id / "natives"
        target.mkdir(parents=True, exist_ok=True)
        for path, meta in natives:
            archive = self.game_dir / "libraries" / path
            if not archive.is_file():
                continue
            with zipfile.ZipFile(archive) as zf:
                for info in zf.infolist():
                    name = info.filename
                    if name.endswith("/") or name.startswith("META-INF/"):
                        continue
                    if self._excluded(version, name):
                        continue
                    out = (target / name).resolve()
                    # 目录祖先关系才是边界；字符串前缀会放行 ../natives-other/。
                    if not out.is_relative_to(target.resolve()):
                        raise InstallError(f"原生库包含越界路径：{name}")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(out, "wb") as dst:
                        dst.write(src.read())

    @staticmethod
    def _excluded(version: Version, name: str) -> bool:
        for lib in version.raw.get("libraries", []):
            extract = lib.get("extract") or {}
            for pattern in extract.get("exclude") or []:
                if fnmatch.fnmatch(name, pattern):
                    return True
        return False

    @staticmethod
    def _report(progress: Progress | None, phase: str, done: int, total: int) -> None:
        if progress:
            progress(phase, done, total)


def fetch_json_bytes(url: str) -> bytes:
    from .net import fetch_bytes

    return fetch_bytes(url)
