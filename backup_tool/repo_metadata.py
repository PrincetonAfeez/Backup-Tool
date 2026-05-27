"""Repository-level metadata written to repo.json."""

from __future__ import annotations

from datetime import UTC, datetime

REPO_VERSION = 1
HASH_ALGORITHM = "sha256"
STORAGE = "content-addressable"
OBJECT_LAYOUT = "sha256-prefix-2"
CHUNKING = "fixed-1mb-blocks-above-threshold"


def format_created_at(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return now.isoformat(timespec="microseconds").replace("+00:00", "Z")


def validate_repo_version(value: object) -> list[str]:
    if isinstance(value, bool) or not isinstance(value, int):
        return ["Repository version must be an integer"]
    if value != REPO_VERSION:
        return [f"Unsupported repo version: {value}"]
    return []


def default_repo_metadata(now: datetime | None = None) -> dict[str, object]:
    return {
        "version": REPO_VERSION,
        "created_at": format_created_at(now),
        "hash_algorithm": HASH_ALGORITHM,
        "storage": STORAGE,
        "object_layout": OBJECT_LAYOUT,
        "chunking": CHUNKING,
    }


def validate_repo_metadata(metadata: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(metadata, dict):
        return ["Repository metadata root must be an object"]
    errors.extend(validate_repo_version(metadata.get("version")))
    if metadata.get("hash_algorithm") != HASH_ALGORITHM:
        errors.append("Repository hash algorithm is not sha256")
    if metadata.get("storage") != STORAGE:
        errors.append(f"Unsupported storage: {metadata.get('storage')!r}")
    if metadata.get("object_layout") != OBJECT_LAYOUT:
        errors.append(f"Unsupported object_layout: {metadata.get('object_layout')!r}")
    if metadata.get("chunking") != CHUNKING:
        errors.append(f"Unsupported chunking: {metadata.get('chunking')!r}")
    return errors
