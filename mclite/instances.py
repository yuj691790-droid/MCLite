"""Discovering installed versions in the main game dir and instances."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class InstalledVersion:
    label: str
    root: Path
    version_id: str


def scan_versions(game_dir: Path) -> list[InstalledVersion]:
    """Return every version with a JSON, main dir first, then instances."""
    game_dir = Path(game_dir)
    roots: list[tuple[str, Path]] = [("", game_dir)]
    instances = game_dir / "instances"
    if instances.is_dir():
        roots += [(child.name, child) for child in sorted(instances.iterdir()) if child.is_dir()]

    found: list[InstalledVersion] = []
    for prefix, root in roots:
        versions_dir = root / "versions"
        if not versions_dir.is_dir():
            continue
        for child in sorted(versions_dir.iterdir()):
            if not (child / f"{child.name}.json").is_file():
                continue
            label = f"{prefix} / {child.name}" if prefix else child.name
            found.append(InstalledVersion(label=label, root=root, version_id=child.name))
    return found
