"""Restore file metadata (mode, mtime) from manifest entries."""

from __future__ import annotations

import os
from pathlib import Path

from backup_tool.manifest import FileEntry


def restore_entry_metadata(path: Path, entry: FileEntry, warnings: list[str]) -> None:
    if entry.mode is not None:
        try:
            os.chmod(path, entry.mode)
        except OSError as exc:
            warnings.append(f"Could not restore mode for {path}: {exc}")
    if entry.mtime is not None:
        try:
            os.utime(path, (entry.mtime, entry.mtime))
        except OSError as exc:
            warnings.append(f"Could not restore mtime for {path}: {exc}")
