"""Fixed-size block chunking for large files."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from backup_tool.errors import HashError, StoreError
from backup_tool.hashing import DEFAULT_CHUNK_SIZE
from backup_tool.object_store import ObjectStore


# Files larger than this threshold are stored as content-defined fixed blocks.
CHUNKING_THRESHOLD = DEFAULT_CHUNK_SIZE


@dataclass(frozen=True)
class StoredFileInfo:
    """Result of storing or planning storage for one source file."""

    hash_hex: str
    size: int
    chunks: tuple[str, ...] | None
    new_blob_count: int
    bytes_stored: int


def file_blob_hashes(entry) -> list[str]:
    """Return object-store hashes referenced by a manifest file entry."""

    if entry.type != "file":
        return []
    if entry.chunks:
        return list(entry.chunks)
    if entry.hash:
        return [entry.hash]
    return []


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
            from backup_tool.hashing import hash_file

            result = hash_file(path, chunk_size=chunk_size)
            stored_new = not store.exists(result.hash_hex)
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
    file_digest = sha256()

    try:
        with path.open("rb") as src:
            while True:
                data = src.read(chunk_size)
                if not data:
                    break
                file_digest.update(data)
                chunk_hash = sha256(data).hexdigest()
                chunks.append(chunk_hash)

                if dry_run:
                    if not store.exists(chunk_hash):
                        new_blob_count += 1
                        bytes_stored += len(data)
                    continue

                blob = store.put_bytes(data)
                if blob.stored_new_blob:
                    new_blob_count += 1
                    bytes_stored += blob.bytes_stored
    except OSError as exc:
        raise StoreError(f"Could not store {path}: {exc}") from exc

    return StoredFileInfo(
        file_digest.hexdigest(),
        size,
        tuple(chunks),
        new_blob_count,
        bytes_stored,
    )


def verify_file_content(store: ObjectStore, entry) -> bool:
    """Verify a manifest file entry against the object store."""

    if entry.type != "file" or not entry.hash:
        return False

    if entry.chunks:
        digest = sha256()
        for chunk_hash in entry.chunks:
            if not store.verify_blob(chunk_hash):
                return False
            with store.open_blob(chunk_hash, "rb") as chunk_file:
                while True:
                    data = chunk_file.read(DEFAULT_CHUNK_SIZE)
                    if not data:
                        break
                    digest.update(data)
        return digest.hexdigest() == entry.hash

    return store.verify_blob(entry.hash)


def restore_file_content(store: ObjectStore, entry, target: Path) -> None:
    """Write one manifest file entry to a destination path."""

    if entry.type != "file" or not entry.hash:
        raise StoreError("File entry is missing hash")

    target.parent.mkdir(parents=True, exist_ok=True)

    if entry.chunks:
        digest = sha256()
        with target.open("wb") as dst:
            for chunk_hash in entry.chunks:
                if not store.verify_blob(chunk_hash):
                    raise StoreError(f"Chunk failed verification: {chunk_hash}")
                with store.open_blob(chunk_hash, "rb") as src:
                    while True:
                        data = src.read(DEFAULT_CHUNK_SIZE)
                        if not data:
                            break
                        digest.update(data)
                        dst.write(data)
        if digest.hexdigest() != entry.hash:
            raise HashError(f"Restored file hash mismatch: {target}")
        return

    if not store.verify_blob(entry.hash):
        raise StoreError(f"Blob failed verification: {entry.hash}")

    with store.open_blob(entry.hash, "rb") as src, target.open("wb") as dst:
        while True:
            data = src.read(DEFAULT_CHUNK_SIZE)
            if not data:
                break
            dst.write(data)

    from backup_tool.hashing import hash_file

    if hash_file(target).hash_hex != entry.hash:
        raise HashError(f"Restored file hash mismatch: {target}")
