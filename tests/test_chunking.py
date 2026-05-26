"""Tests for backup_tool.chunking."""

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from backup_tool.chunking import (
    CHUNKING_THRESHOLD,
    StoredFileInfo,
    file_blob_hashes,
    restore_file_content,
    store_file,
    verify_file_content,
    verify_file_entry,
)
from backup_tool.errors import IntegrityError, ManifestError, StoreError
from backup_tool.manifest import FileEntry
from backup_tool.object_store import ObjectStore
from tests.conftest import manifest_hash


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    object_store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    object_store.init()
    return object_store


def test_file_blob_hashes_branches():
    assert file_blob_hashes(FileEntry(type="symlink", target="x")) == []
    assert file_blob_hashes(FileEntry(type="file", hash=manifest_hash("h"))) == [manifest_hash("h")]
    assert file_blob_hashes(FileEntry(type="file", hash=manifest_hash("h"), chunks=(manifest_hash("c1"),))) == [
        manifest_hash("c1")
    ]
    with pytest.raises(ManifestError):
        FileEntry(type="file")


def test_store_small_file_whole(store: ObjectStore, tmp_path: Path):
    path = tmp_path / "small.txt"
    path.write_text("tiny", encoding="utf-8")
    info = store_file(store, path)
    assert info.chunks is None
    assert store.exists(info.hash_hex)


def test_store_large_file_chunked(store: ObjectStore, tmp_path: Path):
    payload = (b"L" * CHUNKING_THRESHOLD) + b"tail"
    path = tmp_path / "large.bin"
    path.write_bytes(payload)
    info = store_file(store, path)
    assert info.chunks is not None
    assert len(info.chunks) == 2
    assert info.hash_hex == sha256(payload).hexdigest()


def test_store_file_dry_run(store: ObjectStore, tmp_path: Path):
    path = tmp_path / "small.txt"
    path.write_text("data", encoding="utf-8")
    info = store_file(store, path, dry_run=True)
    assert isinstance(info, StoredFileInfo)
    assert store.iter_hashes() == []


def test_store_file_stat_error(store: ObjectStore, tmp_path: Path):
    with pytest.raises(StoreError, match="Could not stat"):
        store_file(store, tmp_path / "missing.bin")


def test_shared_chunks_deduplicate(store: ObjectStore, tmp_path: Path):
    shared = b"S" * CHUNKING_THRESHOLD
    path_a = tmp_path / "a.bin"
    path_b = tmp_path / "b.bin"
    path_a.write_bytes(shared + b"aaa")
    path_b.write_bytes(shared + b"bbb")
    info_a = store_file(store, path_a)
    info_b = store_file(store, path_b)
    assert info_a.chunks[0] == info_b.chunks[0]
    assert len(store.iter_hashes()) == 3


def test_verify_and_restore_whole_file(store: ObjectStore, tmp_path: Path):
    source = tmp_path / "src.bin"
    target = tmp_path / "out.bin"
    source.write_bytes(b"payload")
    info = store_file(store, source)
    entry = FileEntry(type="file", hash=info.hash_hex, size=info.size)
    assert verify_file_content(store, entry)
    restore_file_content(store, entry, target)
    assert target.read_bytes() == b"payload"


def test_verify_and_restore_chunked_file(store: ObjectStore, tmp_path: Path):
    payload = (b"C" * CHUNKING_THRESHOLD) + b"end"
    source = tmp_path / "src.bin"
    target = tmp_path / "out.bin"
    source.write_bytes(payload)
    info = store_file(store, source)
    entry = FileEntry(type="file", hash=info.hash_hex, size=info.size, chunks=info.chunks)
    assert verify_file_content(store, entry)
    restore_file_content(store, entry, target)
    assert target.read_bytes() == payload


def test_verify_file_content_false_cases(store: ObjectStore):
    assert verify_file_content(store, FileEntry(type="symlink", target="x")) is False
    assert verify_file_content(store, SimpleNamespace(type="file", hash=None, chunks=None)) is False


def test_verify_file_entry_distinguishes_missing_and_mismatch(store: ObjectStore):
    missing_hash = manifest_hash("missing")
    with pytest.raises(IntegrityError, match="Missing blob"):
        verify_file_entry(store, FileEntry(type="file", hash=missing_hash, size=1))

    blob = store.put_bytes(b"payload")
    corrupt_entry = FileEntry(type="file", hash=blob.hash_hex, size=blob.size)
    store.get_path(blob.hash_hex).write_text("bad", encoding="utf-8")
    with pytest.raises(IntegrityError, match="Hash mismatch"):
        verify_file_entry(store, corrupt_entry)


def test_restore_file_content_errors(store: ObjectStore, tmp_path: Path):
    entry = FileEntry(type="file", hash=sha256(b"x").hexdigest())
    with pytest.raises(StoreError, match="missing hash"):
        restore_file_content(store, SimpleNamespace(type="file", hash=None), tmp_path / "x")
    with pytest.raises(IntegrityError, match="Missing blob"):
        restore_file_content(store, entry, tmp_path / "x")
