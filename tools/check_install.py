"""Verify an installed version can be assembled into a runnable command."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mclite.java import find_javas, pick_java, required_java_major
from mclite.launch import AuthData, build_command
from mclite.meta import Version


def main() -> int:
    game = Path(sys.argv[1])
    version_id = sys.argv[2] if len(sys.argv) > 2 else "1.20.1"
    version = Version.load(version_id, game)
    java = pick_java(find_javas(), required_java_major(version_id))
    if java is None:
        print("FAIL: no java")
        return 1
    spec = build_command(
        version, game, java, AuthData("Steve", "0" * 32, "token"), memory_mb=1024
    )
    missing = [p for p in spec.classpath if not Path(p).is_file()]
    natives_dir = game / "versions" / version.id / "natives"
    natives = [p for p in natives_dir.rglob("*") if p.is_file()] if natives_dir.is_dir() else []
    assets_dir = game / "assets" / "indexes"
    indexes = list(assets_dir.glob("*.json")) if assets_dir.is_dir() else []
    print(f"version      : {version.id}")
    print(f"main class   : {version.main_class}")
    print(f"classpath    : {len(spec.classpath)} entries, {len(missing)} missing")
    for path in missing[:5]:
        print("  missing:", path)
    print(f"natives      : {len(natives)} files in {natives_dir}")
    print(f"asset index  : {[p.name for p in indexes]}")
    print(f"command      : {' '.join(spec.command[:8])} ...")
    return 0 if not missing and natives else 2


if __name__ == "__main__":
    raise SystemExit(main())
