"""Transaction staging for snapshot blob writes."""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from typing import Any

from backup_tool.errors import ManifestError, StoreError


MANIFEST_STAT_KEYS = frozenset(
    {
        "entry_count",
        "regular_file_count",
        "directory_count",
        "symlink_count",
        "total_bytes_scanned",
        "new_bytes_stored",
        "new_blobs",
        "new_files",
        "changed_files",
        "deleted_files",
        "unchanged_files",
        "skipped_files",
        "errors",
    }
)

LEGACY_STAT_ALIASES = {"file_count": "entry_count"}

SNAPSHOT_ID_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-\d+Z_[0-9a-f]{8}$"
)
CREATED_AT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"
)


def validate_snapshot_id(snapshot_id: str) -> str:
    if not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
        raise ManifestError(f"Invalid snapshot id: {snapshot_id}")
    return snapshot_id


def validate_created_at(created_at: str) -> str:
    if not CREATED_AT_RE.fullmatch(created_at):
        raise ManifestError(f"Invalid manifest created_at: {created_at}")
    return created_at


def validate_manifest_stats(stats: Any) -> dict[str, int]:
    if not isinstance(stats, dict):
        raise ManifestError("Manifest stats must be an object")
    normalized = dict(stats)
    for legacy_key, current_key in LEGACY_STAT_ALIASES.items():
        if legacy_key in normalized and current_key not in normalized:
            normalized[current_key] = normalized.pop(legacy_key)
    validated: dict[str, int] = {}
    for key, value in normalized.items():
        if key not in MANIFEST_STAT_KEYS:
            raise ManifestError(f"Unsupported manifest stat key: {key}")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ManifestError(f"Manifest stat {key} must be an integer")
        if value < 0:
            raise ManifestError(f"Manifest stat {key} must be >= 0")
        validated[key] = value
    return validated


def validate_skipped_items(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ManifestError("Manifest skipped must be a list")
    skipped: list[dict[str, str]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ManifestError(f"Manifest skipped[{index}] must be an object")
        path = item.get("path")
        reason = item.get("reason")
        if not isinstance(path, str) or not isinstance(reason, str):
            raise ManifestError(
                f"Manifest skipped[{index}] must contain string path and reason"
            )
        skipped.append({"path": path, "reason": reason})
    return skipped


def staging_snapshot_id(now: datetime, token: str) -> str:
    stamp = now.strftime("%Y-%m-%dT%H-%M-%S-%fZ")
    return validate_snapshot_id(f"{stamp}_{token}")


def validate_staging_snapshot_id(snapshot_id: str) -> str:
    if "/" in snapshot_id or "\\" in snapshot_id or snapshot_id in ("", ".", ".."):
        raise StoreError(f"Invalid staging snapshot id: {snapshot_id}")
    validate_snapshot_id(snapshot_id)
    return snapshot_id
