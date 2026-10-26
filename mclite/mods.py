"""Mod search and download: Modrinth (no key) and CurseForge (API key)."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from .net import Downloader, DownloadTask, fetch_json

MODRINTH = "https://api.modrinth.com/v2"
CURSEFORGE = "https://api.curseforge.com/v1"

LOADER_TAGS = {
    "fabric": "fabric",
    "forge": "forge",
    "neoforge": "neoforge",
    "quilt": "quilt",
}

CF_LOADER_IDS = {"forge": 1, "fabric": 4, "quilt": 5, "neoforge": 6}


class ModError(Exception):
    pass


@dataclass
class ModHit:
    source: str
    id: str
    name: str
    description: str
    author: str = ""
    downloads: int = 0
    icon_url: str = ""


@dataclass
class ModFile:
    url: str
    filename: str
    sha1: str | None = None
    size: int | None = None


class ModrinthClient:
    def __init__(self, downloader: Downloader | None = None) -> None:
        self.downloader = downloader or Downloader()

    def search(
        self,
        query: str,
        *,
        game_version: str | None = None,
        loader: str | None = None,
        limit: int = 20,
    ) -> list[ModHit]:
        facets: list[list[str]] = [["project_type:mod"]]
        if game_version:
            facets.append([f"versions:{game_version}"])
        if loader and loader in LOADER_TAGS:
            facets.append([f"categories:{LOADER_TAGS[loader]}"])
        params = {
            "query": query,
            "limit": str(limit),
            "index": "relevance",
            "facets": json.dumps(facets),
        }
        data = fetch_json(f"{MODRINTH}/search?" + urllib.parse.urlencode(params))
        hits: list[ModHit] = []
        for raw in data.get("hits", []):
            hits.append(
                ModHit(
                    source="modrinth",
                    id=raw.get("project_id", ""),
                    name=raw.get("title", ""),
                    description=raw.get("description", ""),
                    author=raw.get("author", ""),
                    downloads=int(raw.get("downloads", 0) or 0),
                    icon_url=raw.get("icon_url", "") or "",
                )
            )
        return hits

    def versions(self, project_id: str, *, game_version: str | None, loader: str | None) -> list[dict]:
        query: dict[str, str] = {}
        if loader and loader in LOADER_TAGS:
            query["loaders"] = json.dumps([LOADER_TAGS[loader]])
        if game_version:
            query["game_versions"] = json.dumps([game_version])
        suffix = "?" + urllib.parse.urlencode(query) if query else ""
        return fetch_json(f"{MODRINTH}/project/{project_id}/version{suffix}")

    def install(
        self,
        project_id: str,
        mods_dir: Path,
        *,
        game_version: str | None = None,
        loader: str | None = None,
        resolve_dependencies: bool = True,
    ) -> list[Path]:
        mods_dir = Path(mods_dir)
        mods_dir.mkdir(parents=True, exist_ok=True)
        installed: list[Path] = []
        visited: set[str] = set()

        def install_one(pid: str) -> None:
            if pid in visited:
                return
            visited.add(pid)
            versions = self.versions(pid, game_version=game_version, loader=loader)
            if not versions:
                raise ModError(f"Modrinth 项目 {pid} 没有匹配的版本")
            version = versions[0]
            files = version.get("files") or []
            primary = next((f for f in files if f.get("primary")), files[0] if files else None)
            if not primary:
                raise ModError(f"Modrinth 项目 {pid} 没有可下载文件")
            dest = mods_dir / primary["filename"]
            task = DownloadTask(
                url=primary["url"],
                dest=dest,
                sha1=(primary.get("hashes") or {}).get("sha1"),
                size=primary.get("size"),
            )
            self.downloader.ensure(task)
            installed.append(dest)
            if resolve_dependencies:
                for dep in version.get("dependencies") or []:
                    if dep.get("dependency_type") == "required" and dep.get("project_id"):
                        install_one(dep["project_id"])

        install_one(project_id)
        return installed


class CurseForgeClient:
    """CurseForge's API requires a key from console.curseforge.com."""

    GAME_ID = 432
    MODS_CLASS = 6

    def __init__(self, api_key: str, downloader: Downloader | None = None) -> None:
        if not api_key:
            raise ModError("CurseForge 需要 API Key，请在设置中填写。")
        self.api_key = api_key
        self.downloader = downloader or Downloader()

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{CURSEFORGE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return fetch_json(url, headers={"x-api-key": self.api_key})

    def search(
        self,
        query: str,
        *,
        game_version: str | None = None,
        loader: str | None = None,
        limit: int = 20,
    ) -> list[ModHit]:
        params: dict = {
            "gameId": self.GAME_ID,
            "classId": self.MODS_CLASS,
            "searchFilter": query,
            "pageSize": limit,
            "sortField": 2,
            "sortOrder": "desc",
        }
        if game_version:
            params["gameVersion"] = game_version
        if loader in CF_LOADER_IDS:
            params["modLoaderType"] = CF_LOADER_IDS[loader]
        data = self._get("/mods/search", params)
        hits: list[ModHit] = []
        for raw in data.get("data", []):
            hits.append(
                ModHit(
                    source="curseforge",
                    id=str(raw.get("id", "")),
                    name=raw.get("name", ""),
                    description=raw.get("summary", ""),
                    author=(raw.get("authors") or [{}])[0].get("name", ""),
                    downloads=int(raw.get("downloadCount", 0) or 0),
                    icon_url=(raw.get("logo") or {}).get("thumbnailUrl", "") or "",
                )
            )
        return hits

    def file_info(self, mod_id: str, file_id: int) -> dict:
        data = self._get(f"/mods/{mod_id}/files/{file_id}")
        return data.get("data") or {}

    def install(
        self,
        mod_id: str,
        mods_dir: Path,
        *,
        game_version: str | None = None,
        loader: str | None = None,
    ) -> list[Path]:
        params: dict = {"pageSize": 50}
        if game_version:
            params["gameVersion"] = game_version
        if loader in CF_LOADER_IDS:
            params["modLoaderType"] = CF_LOADER_IDS[loader]
        data = self._get(f"/mods/{mod_id}/files", params)
        files = data.get("data") or []
        if not files:
            raise ModError("CurseForge 没有找到匹配的游戏版本文件")
        files.sort(key=lambda f: str(f.get("fileDate", "")), reverse=True)
        chosen = files[0]
        url = chosen.get("downloadUrl")
        if not url:
            raise ModError("该作者禁止第三方启动器下载（downloadUrl 为空），请到 CurseForge 网页手动下载。")
        dest = Path(mods_dir) / chosen["fileName"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        sha1 = next(
            (h.get("value") for h in (chosen.get("hashes") or []) if h.get("algo") == 1),
            None,
        )
        task = DownloadTask(url=url, dest=dest, sha1=sha1, size=chosen.get("fileLength"))
        self.downloader.ensure(task)
        return [dest]
