"""Content-addressable object storage."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO

from backup_tool.atomic import fsync_directory
from backup_tool.errors import IntegrityError, StoreError
from backup_tool.hashing import DEFAULT_CHUNK_SIZE, hash_file


@dataclass(frozen=True)
class BlobInfo:
    hash_hex: str
    size: int
    stored_new_blob: bool
    bytes_stored: int


def validate_hash(hash_hex: str) -> str:
    if len(hash_hex) != 64:
        raise StoreError(f"Invalid SHA-256 hash length: {hash_hex}")
    try:
        int(hash_hex, 16)
    except ValueError as exc:
        raise StoreError(f"Invalid SHA-256 hash: {hash_hex}") from exc
    return hash_hex.lower()


class ObjectStore:
    """Store raw bytes by SHA-256 content hash."""

    def __init__(self, objects_dir: Path, tmp_dir: Path | None = None):
        self.objects_dir = objects_dir
        self.tmp_dir = tmp_dir or objects_dir.parent / "tmp"

    def init(self) -> None:
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    def get_path(self, hash_hex: str) -> Path:
        hash_hex = validate_hash(hash_hex)
        return self.objects_dir / hash_hex[:2] / hash_hex

    def exists(self, hash_hex: str) -> bool:
        return self.get_path(hash_hex).is_file()

    def put_bytes(self, data: bytes) -> BlobInfo:
        hash_hex = sha256(data).hexdigest()
        final_path = self.get_path(hash_hex)
        if final_path.exists():
            return BlobInfo(hash_hex, len(data), False, 0)

        final_path.parent.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".blob.", suffix=".tmp", dir=self.tmp_dir)
        temp_path = Path(temp_name)

        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, final_path)
            fsync_directory(final_path.parent)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

        return BlobInfo(hash_hex, len(data), True, len(data))

    def put_file(self, path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> BlobInfo:
        """Stream a file once into a temp blob while computing its hash."""

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".blob.", suffix=".tmp", dir=self.tmp_dir)
        temp_path = Path(temp_name)
        digest = sha256()
        size = 0

        try:
            with path.open("rb") as src, os.fdopen(fd, "wb") as dst:
                while True:
                    chunk = src.read(chunk_size)
                    if not chunk:
                        break
                    digest.update(chunk)
                    dst.write(chunk)
                    size += len(chunk)
                dst.flush()
                os.fsync(dst.fileno())

            hash_hex = digest.hexdigest()
            final_path = self.get_path(hash_hex)
            if final_path.exists():
                temp_path.unlink(missing_ok=True)
                return BlobInfo(hash_hex, size, False, 0)

            final_path.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, final_path)
            fsync_directory(final_path.parent)
            return BlobInfo(hash_hex, size, True, size)
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            temp_path.unlink(missing_ok=True)
            raise StoreError(f"Could not store {path}: {exc}") from exc
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    def open_blob(self, hash_hex: str, mode: str = "rb") -> BinaryIO:
        if "b" not in mode:
            raise StoreError("Blobs must be opened in binary mode")
        return self.get_path(hash_hex).open(mode)

    def verify_blob(self, hash_hex: str) -> bool:
        hash_hex = validate_hash(hash_hex)
        path = self.get_path(hash_hex)
        if not path.exists():
            raise IntegrityError(f"Missing blob: {hash_hex}")
        return hash_file(path).hash_hex == hash_hex

    def iter_hashes(self) -> list[str]:
        if not self.objects_dir.exists():
            return []

        hashes: list[str] = []
        for path in self.objects_dir.glob("*/*"):
            if path.is_file():
                try:
                    hashes.append(validate_hash(path.name))
                except StoreError:
                    continue
        return sorted(hashes)

    def iter_malformed_paths(self) -> list[Path]:
        malformed: list[Path] = []
        if not self.objects_dir.exists():
            return malformed

        for path in self.objects_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                hash_hex = validate_hash(path.name)
            except StoreError:
                malformed.append(path)
                continue
            expected_parent = hash_hex[:2]
            if path.parent.name != expected_parent:
                malformed.append(path)
        return malformed
