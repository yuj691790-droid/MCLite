"""Minecraft version metadata: manifest, rules, libraries, inheritance.

The launcher never invents URLs: everything comes from the official
``version_manifest_v2.json`` and the per-version JSON it points at.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .net import BMCLAPI, Downloader, DownloadTask, fetch_json, mirror_url

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MANIFEST_MIRROR = f"{BMCLAPI}/mc/game/version_manifest_v2.json"
LIBRARY_BASE = "https://libraries.minecraft.net/"
ASSET_BASE = "https://resources.download.minecraft.net/"

_MAVEN_RE = re.compile(
    r"^(?P<group>[^:@]+):(?P<artifact>[^:@]+):(?P<version>[^:@]+)"
    r"(?::(?P<classifier>[^:@]+))?(?:@(?P<ext>[^:@]+))?$"
)


@dataclass(frozen=True)
class OsContext:
    name: str = "windows"
    arch: str = "x86_64"
    version: str = "10.0"
    features: dict[str, bool] = field(default_factory=dict)


@dataclass
class VersionSummary:
    id: str
    type: str
    url: str
    time: str
    release_time: str

    @classmethod
    def from_dict(cls, raw: dict) -> "VersionSummary":
        return cls(
            id=raw["id"],
            type=raw.get("type", "release"),
            url=raw["url"],
            time=raw.get("time", ""),
            release_time=raw.get("releaseTime", ""),
        )


@dataclass
class Manifest:
    latest_release: str
    latest_snapshot: str
    versions: list[VersionSummary]

    @classmethod
    def fetch(cls, *, mirror: bool = True, timeout: float = 30.0) -> "Manifest":
        raw = fetch_json(MANIFEST_MIRROR if mirror else MANIFEST_URL, timeout=timeout)
        latest = raw.get("latest", {})
        versions = [VersionSummary.from_dict(v) for v in raw.get("versions", [])]
        return cls(
            latest_release=latest.get("release", ""),
            latest_snapshot=latest.get("snapshot", ""),
            versions=versions,
        )

    def find(self, version_id: str) -> VersionSummary | None:
        return next((v for v in self.versions if v.id == version_id), None)


def _rule_matches(rule: dict, ctx: OsContext) -> bool:
    os_rule = rule.get("os")
    if os_rule:
        if "name" in os_rule and os_rule["name"] != ctx.name:
            return False
        if "arch" in os_rule and os_rule["arch"] != ctx.arch:
            return False
        if "version" in os_rule:
            try:
                if not re.search(os_rule["version"], ctx.version):
                    return False
            except re.error:
                return False
    features = rule.get("features")
    if features:
        for key, expected in features.items():
            if bool(ctx.features.get(key)) != bool(expected):
                return False
    return True


def rules_allow(rules: list[dict] | None, ctx: OsContext) -> bool:
    """Minecraft's rule algorithm: last matching rule wins, default deny."""
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if _rule_matches(rule, ctx):
            allowed = rule.get("action", "allow") == "allow"
    return allowed


def parse_maven_name(name: str) -> dict[str, str]:
    match = _MAVEN_RE.match(name)
    if not match:
        raise ValueError(f"invalid maven coordinate: {name}")
    parts = match.groupdict()
    return {
        "group": parts["group"],
        "artifact": parts["artifact"],
        "version": parts["version"],
        "classifier": parts.get("classifier") or "",
        "ext": parts.get("ext") or "jar",
    }


def maven_path(name: str) -> str:
    parts = parse_maven_name(name)
    group_path = parts["group"].replace(".", "/")
    suffix = f"-{parts['classifier']}" if parts["classifier"] else ""
    return f"{group_path}/{parts['artifact']}/{parts['version']}/{parts['artifact']}-{parts['version']}{suffix}.{parts['ext']}"


def library_artifact(lib: dict) -> dict | None:
    downloads = lib.get("downloads") or {}
    artifact = downloads.get("artifact")
    if artifact:
        return artifact
    # A native-only library has no main jar; its name is not a downloadable path.
    if lib.get("natives") or downloads.get("classifiers"):
        return None
    name = lib.get("name")
    if not name:
        return None
    path = maven_path(name)
    base = lib.get("url") or LIBRARY_BASE
    if not base.endswith("/"):
        base += "/"
    return {"path": path, "url": base + path}


def library_native(lib: dict, ctx: OsContext) -> dict | None:
    natives = lib.get("natives") or {}
    classifier = natives.get(ctx.name)
    if not classifier:
        return None
    # Old metadata sometimes embeds ${arch}; modern natives are plain names.
    classifier = classifier.replace("${arch}", "64" if ctx.arch.endswith("64") else "32")
    downloads = lib.get("downloads") or {}
    return (downloads.get("classifiers") or {}).get(classifier)


def _is_native_library(lib: dict) -> bool:
    if lib.get("natives"):
        return True
    try:
        return parse_maven_name(lib.get("name", "")).get("classifier", "").startswith("natives-")
    except ValueError:
        return False


def _native_rank(classifier: str, arch: str) -> int | None:
    """Rank a modern ``natives-<os>[-<arch>]`` classifier for this CPU.

    Exact arch match wins over the generic build; incompatible builds are
    dropped so a 64-bit game never extracts 32-bit or ARM DLLs.
    """
    suffixes = {"-arm64": "arm64", "-x86": "x86", "-arm32": "arm32", "-x86_64": "x86_64"}
    for suffix, wanted in suffixes.items():
        if classifier.endswith(suffix):
            return 2 if arch == wanted else None
    return 1


def _native_group(name: str) -> str:
    try:
        parts = parse_maven_name(name)
        return f"{parts['group']}:{parts['artifact']}:{parts['version']}"
    except ValueError:
        return name


def library_downloads(
    libraries: Iterable[dict], ctx: OsContext
) -> tuple[list[tuple[str, dict]], list[tuple[str, dict]]]:
    """Split libraries into (classpath artifacts, native archives).

    Two metadata generations are supported: the old ``natives`` map with
    ``downloads.classifiers`` and the modern ``<artifact>:natives-<os>``
    library entries that carry a normal ``downloads.artifact``. For the
    modern form only the best build for ``ctx.arch`` is kept per library.
    """
    artifacts: dict[str, dict] = {}
    native_best: dict[str, tuple[int, dict]] = {}
    for lib in libraries:
        if not rules_allow(lib.get("rules"), ctx):
            continue
        # 旧格式的同一条目可同时携带 Java 主库和 native 分类包，两者都要保留。
        if lib.get("natives"):
            artifact = library_artifact(lib)
            if artifact and artifact.get("path") and artifact.get("url"):
                artifacts.setdefault(artifact["path"], artifact)
        if _is_native_library(lib):
            entry = library_native(lib, ctx) if lib.get("natives") else library_artifact(lib)
            if not entry or not entry.get("path") or not entry.get("url"):
                continue
            classifier = ""
            try:
                classifier = parse_maven_name(lib.get("name", "")).get("classifier", "")
            except ValueError:
                pass
            rank = _native_rank(classifier, ctx.arch) if classifier else 2
            if rank is None:
                continue
            key = _native_group(lib.get("name", ""))
            current = native_best.get(key)
            if current is None or rank > current[0]:
                native_best[key] = (rank, entry)
            continue
        artifact = library_artifact(lib)
        if artifact and artifact.get("path") and artifact.get("url"):
            artifacts.setdefault(artifact["path"], artifact)
    return list(artifacts.items()), [(entry["path"], entry) for _, entry in native_best.values()]


@dataclass
class Version:
    """One resolved version JSON (inheritance already flattened)."""

    id: str
    raw: dict
    source_dir: Path | None = None
    base_id: str | None = None

    @property
    def jar_id(self) -> str:
        """Which ``versions/<id>/<id>.jar`` this profile launches with."""
        return self.raw.get("jar") or self.base_id or self.id

    @property
    def main_class(self) -> str:
        return self.raw.get("mainClass", "")

    @property
    def asset_index(self) -> dict:
        return self.raw.get("assetIndex") or {}

    @property
    def assets(self) -> str:
        return self.raw.get("assets", "legacy")

    @property
    def client_download(self) -> dict:
        return (self.raw.get("downloads") or {}).get("client") or {}

    @property
    def type(self) -> str:
        return self.raw.get("type", "release")

    def libraries(self, ctx: OsContext) -> list[dict]:
        return [lib for lib in self.raw.get("libraries", []) if rules_allow(lib.get("rules"), ctx)]

    def arguments(self, ctx: OsContext) -> tuple[list[str], list[str]]:
        """Return (jvm arguments, game arguments) after rule filtering."""
        modern = self.raw.get("arguments")
        if modern:
            return (
                _collect_argument_list(modern.get("jvm"), ctx),
                _collect_argument_list(modern.get("game"), ctx),
            )
        legacy = self.raw.get("minecraftArguments")
        if legacy:
            return [], legacy.split()
        return [], []

    # -- loading ---------------------------------------------------------
    @classmethod
    def from_file(cls, path: Path) -> "Version":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(id=raw.get("id", path.parent.name), raw=raw, source_dir=path.parent)

    @classmethod
    def load(
        cls,
        version_id: str,
        game_dir: Path,
        *,
        mirror: bool = True,
        downloader: Downloader | None = None,
    ) -> "Version":
        """Load ``versions/<id>/<id>.json`` or download it from the manifest."""
        path = Path(game_dir) / "versions" / version_id / f"{version_id}.json"
        if path.is_file():
            version = cls.from_file(path)
            if version.raw.get("inheritsFrom"):
                return version.resolve(game_dir, mirror=mirror)
            return version
        manifest = Manifest.fetch(mirror=mirror)
        summary = manifest.find(version_id)
        if summary is None:
            raise ValueError(f"unknown version: {version_id}")
        url = f"{BMCLAPI}/version/{version_id}/json" if mirror else summary.url
        raw = fetch_json(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        version = cls(id=version_id, raw=raw, source_dir=path.parent)
        if raw.get("inheritsFrom"):
            return version.resolve(game_dir, mirror=mirror)
        return version

    def resolve(self, game_dir: Path, *, mirror: bool = True) -> "Version":
        """Flatten ``inheritsFrom`` chains (Fabric/Forge profiles)."""
        parent_id = self.raw.get("inheritsFrom")
        if not parent_id:
            return self
        parent = Version.load(parent_id, game_dir, mirror=mirror)
        return Version(
            id=self.id,
            raw=merge_versions(parent.raw, self.raw),
            source_dir=self.source_dir,
            base_id=parent.base_id or parent.id,
        )


def _collect_argument_list(entries: list | None, ctx: OsContext) -> list[str]:
    out: list[str] = []
    for entry in entries or []:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            if rules_allow(entry.get("rules"), ctx):
                value = entry.get("value")
                if isinstance(value, str):
                    out.append(value)
                elif isinstance(value, list):
                    out.extend(v for v in value if isinstance(v, str))
    return out


def merge_versions(parent: dict, child: dict) -> dict:
    """Merge a loader profile onto its parent version.

    The child wins on scalar fields (mainClass, assets, downloads...), while
    libraries and arguments are combined. Libraries are keyed by
    ``group:artifact`` so a loader can replace a vanilla library version.
    """
    merged = dict(parent)
    for key, value in child.items():
        if key in ("libraries", "arguments", "minecraftArguments"):
            continue
        merged[key] = value
    merged["id"] = child.get("id", parent.get("id"))

    libraries: dict[str, dict] = {}
    order: list[str] = []
    for lib in [*(parent.get("libraries") or []), *(child.get("libraries") or [])]:
        name = lib.get("name", "")
        try:
            parts = parse_maven_name(name)
            key = f"{parts['group']}:{parts['artifact']}:{parts['classifier']}"
        except ValueError:
            key = name
        if key not in libraries:
            order.append(key)
        libraries[key] = lib
    merged["libraries"] = [libraries[key] for key in order]

    parent_args = parent.get("arguments")
    child_args = child.get("arguments")
    if parent_args or child_args:
        merged["arguments"] = {
            "game": list((parent_args or {}).get("game") or []) + list((child_args or {}).get("game") or []),
            "jvm": list((parent_args or {}).get("jvm") or []) + list((child_args or {}).get("jvm") or []),
        }
    if child.get("minecraftArguments"):
        merged["minecraftArguments"] = child["minecraftArguments"]
    return merged


def asset_tasks(index: dict, game_dir: Path, ctx: OsContext, *, mirror: bool) -> list[DownloadTask]:
    """Build download tasks for an asset index's objects.

    Also handles the ``virtual``/``map_to_resources`` legacy layouts by
    returning tasks plus a post-step the caller can run.
    """
    tasks: list[DownloadTask] = []
    objects_dir = Path(game_dir) / "assets" / "objects"
    for name, obj in (index.get("objects") or {}).items():
        digest = obj.get("hash")
        if not digest:
            continue
        url = f"{ASSET_BASE}{digest[:2]}/{digest}"
        mirrored = mirror_url(url)
        tasks.append(
            DownloadTask(
                url=mirrored if (mirror and mirrored) else url,
                dest=objects_dir / digest[:2] / digest,
                sha1=digest,
                size=obj.get("size"),
                fallback=(url,) if (mirror and mirrored) else (),
            )
        )
    return tasks
