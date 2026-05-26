"""Path normalization and restore safety helpers."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from backup_tool.errors import ManifestError, RestoreError


def validate_exclude_pattern(pattern: str) -> str:
    """Validate a backup exclude pattern without manifest path normalization."""

    normalized = pattern.replace("\\", "/")
    if normalized in {"", "."}:
        raise ManifestError("Exclude pattern cannot be empty")
    if ".." in PurePosixPath(normalized).parts:
        raise ManifestError(f"Unsafe exclude pattern: {pattern}")
    return normalized


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
    """Return a safe path under ``destination`` for one manifest entry.

    Preconditions:
    - ``destination`` must be a freshly created staging directory (for example
      a new ``mkdtemp`` tree).
    - No path component under ``destination`` may be a symlink.

    ``Path.resolve()`` is used to detect manifest-path escape. If ``destination``
    or any parent already contained symlinks, ``resolve()`` would follow them and
    the containment check would be unreliable.
    """

    rel = normalize_manifest_path(manifest_path)
    root = destination.resolve()
    target = (root / Path(*PurePosixPath(rel).parts)).resolve()

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RestoreError(f"Manifest path escapes restore destination: {rel}") from exc

    return target


def is_safe_symlink_target(target: str) -> bool:
    """Return True when a symlink target is relative and does not use '..'."""

    if not target:
        return False
    if target.startswith("\\\\"):
        return False
    if len(target) >= 2 and target[1] == ":":
        return False
    pure = PurePosixPath(target.replace("\\", "/"))
    if pure.is_absolute():
        return False
    if ".." in pure.parts:
        return False
    return True


def assert_safe_symlink_target(target: str, *, manifest_path: str) -> None:
    if not is_safe_symlink_target(target):
        raise RestoreError(f"Unsafe symlink target for {manifest_path}: {target}")
