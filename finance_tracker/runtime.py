from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


RUNTIME_FILENAMES = ("config.json", "finance_tracker.sqlite3")


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    root: Path
    data_dir: Path
    temporary_dir: Path
    exports_dir: Path
    backups_dir: Path
    database_path: Path
    config_path: Path


def project_runtime_paths(root: Path | None = None) -> RuntimePaths:
    project_root = (root or Path(__file__).resolve().parent.parent).resolve()
    data_dir = project_root / "data"
    exports_dir = project_root / "exports"
    return RuntimePaths(
        root=project_root,
        data_dir=data_dir,
        temporary_dir=project_root / ".tmp",
        exports_dir=exports_dir,
        backups_dir=exports_dir / "backups",
        database_path=data_dir / "finance_tracker.sqlite3",
        config_path=data_dir / "config.json",
    )


def legacy_runtime_data_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    return base / "FinanceTracker"


def migrate_legacy_runtime_data(
    paths: RuntimePaths,
    legacy_dir: Path | None = None,
    *,
    timestamp: str | None = None,
) -> dict[str, list[str]]:
    source_dir = legacy_dir or legacy_runtime_data_dir()
    source_files = {name: source_dir / name for name in RUNTIME_FILENAMES if (source_dir / name).is_file()}
    if not source_files:
        return {"copied": [], "skipped": []}

    destinations = {
        "config.json": paths.config_path,
        "finance_tracker.sqlite3": paths.database_path,
    }
    existing = [name for name, destination in destinations.items() if destination.exists()]
    if existing:
        return {"copied": [], "skipped": sorted(existing)}

    migration_timestamp = timestamp or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = paths.backups_dir / "migrations" / migration_timestamp
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    backup_dir.mkdir(parents=True, exist_ok=True)

    for name, source in source_files.items():
        backup = backup_dir / name
        destination = destinations[name]
        shutil.copy2(source, backup)
        shutil.copy2(backup, destination)
        if not _same_bytes(source, backup) or not _same_bytes(source, destination):
            raise RuntimeError(f"运行数据迁移校验失败：{name}")

    for source in source_files.values():
        source.unlink()

    return {"copied": sorted(source_files), "skipped": []}


def _same_bytes(left: Path, right: Path) -> bool:
    return _sha256(left) == _sha256(right)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
