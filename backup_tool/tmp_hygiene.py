"""Stale temporary file discovery for repository hygiene."""

from __future__ import annotations

import time
from pathlib import Path

from backup_tool.object_store import DEFAULT_TMP_MAX_AGE_SECONDS


def _is_stale(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime <= cutoff
    except OSError:
        return False


def iter_stale_manifest_tmp_files(
    snapshots_dir: Path,
    max_age_seconds: float = DEFAULT_TMP_MAX_AGE_SECONDS,
) -> list[Path]:
    if not snapshots_dir.exists():
        return []

    cutoff = time.time() - max_age_seconds
    stale: list[Path] = []
    for path in snapshots_dir.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if not (name.endswith(".json.tmp") or name.endswith(".sha256.tmp")):
            continue
        if _is_stale(path, cutoff):
            stale.append(path)
    return sorted(stale)


def iter_stale_lock_tmp_files(
    repo_root: Path,
    max_age_seconds: float = DEFAULT_TMP_MAX_AGE_SECONDS,
) -> list[Path]:
    if not repo_root.is_dir():
        return []

    cutoff = time.time() - max_age_seconds
    stale: list[Path] = []
    for path in repo_root.iterdir():
        if not path.is_file():
            continue
        if not (path.name.startswith(".lock.") and path.name.endswith(".tmp")):
            continue
        if _is_stale(path, cutoff):
            stale.append(path)
    return sorted(stale)


def remove_stale_paths(
    paths: list[Path],
    *,
    dry_run: bool = False,
) -> tuple[list[str], int]:
    removed: list[str] = []
    bytes_deleted = 0
    for path in paths:
        try:
            bytes_deleted += path.stat().st_size
        except OSError:
            pass
        if not dry_run:
            path.unlink(missing_ok=True)
        removed.append(str(path))
    return removed, bytes_deleted
