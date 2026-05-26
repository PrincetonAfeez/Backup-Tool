"""Path normalization and restore safety helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from backup_tool.errors import ManifestError, RestoreError


def normalize_manifest_path(path: str | Path) -> str:
    """Return a safe POSIX-style relative path for a manifest entry."""

    raw = str(path).replace("\\", "/")
    if raw == "" or raw == ".":
        raise ManifestError("Manifest path cannot be empty")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ManifestError(f"Manifest path must be relative: {raw}")

    parts = pure.parts
    if any(part in ("", ".", "..") for part in parts):
        raise ManifestError(f"Unsafe manifest path: {raw}")

    return pure.as_posix()


def source_relative_path(source: Path, path: Path) -> str:
    try:
        relative = path.relative_to(source)
    except ValueError as exc:
        raise ManifestError(f"{path} is not inside source {source}") from exc
    return normalize_manifest_path(relative)


def safe_restore_path(destination: Path, manifest_path: str) -> Path:
    rel = normalize_manifest_path(manifest_path)
    root = destination.resolve()
    target = (root / Path(*PurePosixPath(rel).parts)).resolve()

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RestoreError(f"Manifest path escapes restore destination: {rel}") from exc

    return target
