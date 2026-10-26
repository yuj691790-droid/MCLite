"""Application paths and persisted settings.

Everything lives under one base directory:

    Windows: %APPDATA%\\MCLite
    Other:   ~/.mclite

The game directory defaults to ``<base>/game`` so a fresh install is
self-contained; the user can point it at an existing ``.minecraft`` instead.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import APP_NAME

# MCLite 自有应用；Minecraft Services 准入尚未通过，参见预览版发布说明。
DEFAULT_CLIENT_ID = "584a37d6-641b-4831-8e7d-18a05b5e08db"


def default_base_dir() -> Path:
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return root / APP_NAME
    return Path.home() / (".mclite")


def default_game_dir() -> Path:
    return default_base_dir() / "game"


@dataclass
class Settings:
    """User-visible settings, persisted as JSON."""

    game_dir: str = ""
    java_path: str = ""
    memory_mb: int = 2048
    mirror: bool = True
    curseforge_key: str = ""
    microsoft_client_id: str = DEFAULT_CLIENT_ID
    last_version: str = ""
    extra_jvm_args: str = ""

    def __post_init__(self) -> None:
        if not self.game_dir:
            self.game_dir = str(default_game_dir())

    def normalized(self) -> "Settings":
        self.memory_mb = max(512, min(32768, int(self.memory_mb)))
        if not self.microsoft_client_id.strip():
            self.microsoft_client_id = DEFAULT_CLIENT_ID
        return self


class ConfigStore:
    """Load/save :class:`Settings` next to the launcher data."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir else default_base_dir()
        self.path = self.base_dir / "settings.json"
        self.settings = self.load()

    def load(self) -> Settings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Settings()
        known = {f for f in Settings.__dataclass_fields__}
        return Settings(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(asdict(self.settings), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    @property
    def game_dir(self) -> Path:
        return Path(self.settings.game_dir).expanduser()

    @property
    def downloads_dir(self) -> Path:
        return self.base_dir / "cache"

    @property
    def accounts_file(self) -> Path:
        return self.base_dir / "accounts.json"
