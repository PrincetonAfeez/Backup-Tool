"""Streaming SHA-256 helpers."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from backup_tool.errors import HashError


DEFAULT_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class HashResult:
    hash_hex: str
    size: int
    chunks_read: int


def hash_file(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> HashResult:
    """Hash a file in streaming chunks."""

    digest = sha256()
    size = 0
    chunks = 0

    try:
        with path.open("rb") as file:
            while True:
                chunk = file.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                chunks += 1
    except OSError as exc:
        raise HashError(f"Could not hash {path}: {exc}") from exc

    return HashResult(digest.hexdigest(), size, chunks)
