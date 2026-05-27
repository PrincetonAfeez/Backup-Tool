"""Fixed-size block chunking for large files."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from backup_tool.errors import HashError, IntegrityError, StoreError
from backup_tool.hashing import DEFAULT_CHUNK_SIZE, hash_file
from backup_tool.manifest import FileEntry
from backup_tool.object_store import ObjectStore


# Files larger than this threshold are stored as fixed-size content-addressed blocks.
CHUNKING_THRESHOLD = DEFAULT_CHUNK_SIZE


@dataclass(frozen=True)
class StoredFileInfo:
    """Result of storing or planning storage for one source file."""

    hash_hex: str
    size: int
    chunks: tuple[str, ...] | None
    new_blob_count: int
    bytes_stored: int


def file_blob_hashes(entry: FileEntry) -> list[str]:
    """Return object-store hashes referenced by a manifest file entry."""

    if entry.type != "file":
        return []
    if entry.chunks:
        return list(entry.chunks)
    if entry.hash:
        return [entry.hash]
    return []


def hash_file_content(
    path: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> StoredFileInfo:
    """Hash a file without writing blobs to the object store."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StoreError(f"Could not stat {path}: {exc}") from exc

    if size <= CHUNKING_THRESHOLD:
        result = hash_file(path, chunk_size=chunk_size)
        return StoredFileInfo(result.hash_hex, result.size, None, 0, 0)

    chunks: list[str] = []
    actual_size = 0
    file_digest = sha256()

    try:
        with path.open("rb") as src:
            while True:
                data = src.read(chunk_size)
                if not data:
                    break
                actual_size += len(data)
                file_digest.update(data)
                chunks.append(sha256(data).hexdigest())
    except OSError as exc:
        raise StoreError(f"Could not hash {path}: {exc}") from exc

    return StoredFileInfo(
        file_digest.hexdigest(),
        actual_size,
        tuple(chunks),
        0,
        0,
    )


def store_file(
    store: ObjectStore,
    path: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    dry_run: bool = False,
) -> StoredFileInfo:
    """Store a file whole or as fixed-size chunks when it exceeds the threshold."""

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise StoreError(f"Could not stat {path}: {exc}") from exc

    if size <= CHUNKING_THRESHOLD:
        if dry_run:
            result = hash_file(path, chunk_size=chunk_size)
            stored_new = not store.has_valid_blob(result.hash_hex)
            return StoredFileInfo(
                result.hash_hex,
                result.size,
                None,
                1 if stored_new else 0,
                result.size if stored_new else 0,
            )

        blob = store.put_file(path, chunk_size=chunk_size)
        return StoredFileInfo(
            blob.hash_hex,
            blob.size,
            None,
            1 if blob.stored_new_blob else 0,
            blob.bytes_stored,
        )

    chunks: list[str] = []
    new_blob_count = 0
    bytes_stored = 0
    actual_size = 0
    file_digest = sha256()
    would_store: set[str] = set()

    try:
        with path.open("rb") as src:
            while True:
                data = src.read(chunk_size)
                if not data:
                    break
                actual_size += len(data)
                file_digest.update(data)
                chunk_hash = sha256(data).hexdigest()
                chunks.append(chunk_hash)

                if dry_run:
                    if chunk_hash not in would_store and not store.has_valid_blob(chunk_hash):
                        new_blob_count += 1
                        bytes_stored += len(data)
                    would_store.add(chunk_hash)
                    continue

                blob = store.put_bytes(data)
                if blob.stored_new_blob:
                    new_blob_count += 1
                    bytes_stored += blob.bytes_stored
    except OSError as exc:
        raise StoreError(f"Could not store {path}: {exc}") from exc

    return StoredFileInfo(
        file_digest.hexdigest(),
        actual_size,
        tuple(chunks),
        new_blob_count,
        bytes_stored,
    )


def _iter_blob_bytes(
    store: ObjectStore,
    hash_hex: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Iterator[bytes]:
    """Stream one blob once; shared by verify, restore, and hash checks."""
    path = store.get_path(hash_hex)
    if not path.is_file():
        raise IntegrityError(f"Missing blob: {hash_hex}")
    try:
        with store.open_blob(hash_hex, "rb") as blob:
            while True:
                data = blob.read(chunk_size)
                if not data:
                    break
                yield data
    except OSError as exc:
        raise StoreError(f"Could not read blob {hash_hex}: {exc}") from exc


def _consume_blob(
    store: ObjectStore,
    hash_hex: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[str, int]:
    digest = sha256()
    size = 0
    for data in _iter_blob_bytes(store, hash_hex, chunk_size=chunk_size):
        digest.update(data)
        size += len(data)
    return digest.hexdigest(), size


def verify_file_entry(store: ObjectStore, entry: FileEntry) -> None:
    """Verify a manifest file entry, raising on missing or mismatched blobs."""

    if entry.type != "file" or not entry.hash:
        raise StoreError("File entry is missing hash")

    if entry.chunks:
        file_digest = sha256()
        total_size = 0
        for chunk_hash in entry.chunks:
            chunk_digest = sha256()
            for data in _iter_blob_bytes(store, chunk_hash):
                chunk_digest.update(data)
                file_digest.update(data)
                total_size += len(data)
            if chunk_digest.hexdigest() != chunk_hash:
                raise IntegrityError(f"Hash mismatch for blob: {chunk_hash}")
        if entry.size is not None and total_size != entry.size:
            raise IntegrityError(f"Size mismatch for file entry: {entry.hash}")
        if file_digest.hexdigest() != entry.hash:
            raise IntegrityError(f"Composite hash mismatch for file entry: {entry.hash}")
        return

    blob_digest, total_size = _consume_blob(store, entry.hash)
    if blob_digest != entry.hash:
        raise IntegrityError(f"Hash mismatch for blob: {entry.hash}")
    if entry.size is not None and total_size != entry.size:
        raise IntegrityError(f"Size mismatch for blob: {entry.hash}")


def verify_file_content(store: ObjectStore, entry: FileEntry) -> bool:
    """Verify a manifest file entry against the object store."""

    if entry.type != "file" or not entry.hash:
        return False

    try:
        verify_file_entry(store, entry)
    except (IntegrityError, StoreError):
        return False
    return True


def restore_file_content(store: ObjectStore, entry: FileEntry, target: Path) -> None:
    """Write one manifest file entry to a destination path."""

    if entry.type != "file" or not entry.hash:
        raise StoreError("File entry is missing hash")

    target.parent.mkdir(parents=True, exist_ok=True)

    if entry.chunks:
        file_digest = sha256()
        bytes_written = 0
        with target.open("wb") as dst:
            for chunk_hash in entry.chunks:
                chunk_digest = sha256()
                for data in _iter_blob_bytes(store, chunk_hash):
                    chunk_digest.update(data)
                    file_digest.update(data)
                    dst.write(data)
                    bytes_written += len(data)
                if chunk_digest.hexdigest() != chunk_hash:
                    raise StoreError(f"Chunk failed verification: {chunk_hash}")
        if entry.size is not None and bytes_written != entry.size:
            raise StoreError(
                f"Restored size mismatch for {target}: expected {entry.size}, got {bytes_written}"
            )
        if file_digest.hexdigest() != entry.hash:
            raise HashError(f"Restored file hash mismatch: {target}")
        return

    file_digest = sha256()
    bytes_written = 0
    with target.open("wb") as dst:
        for data in _iter_blob_bytes(store, entry.hash):
            file_digest.update(data)
            dst.write(data)
            bytes_written += len(data)

    if entry.size is not None and bytes_written != entry.size:
        raise StoreError(
            f"Restored size mismatch for {target}: expected {entry.size}, got {bytes_written}"
        )
    if file_digest.hexdigest() != entry.hash:
        raise HashError(f"Restored file hash mismatch: {target}")
