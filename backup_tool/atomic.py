"""Small atomic-write helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def fsync_directory(path: Path) -> None:
    """Best-effort fsync of a directory after replacing a file."""

    if not hasattr(os, "O_DIRECTORY"):
        return

    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return

    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text by fsyncing a temp file and replacing the target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)
        fsync_directory(path.parent)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, text)
