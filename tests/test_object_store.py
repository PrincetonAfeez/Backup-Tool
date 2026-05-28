"""Tests for backup_tool.object_store."""

from hashlib import sha256
from pathlib import Path

import pytest

from backup_tool.errors import IntegrityError, StoreError
from backup_tool.object_store import BlobInfo, ObjectStore, validate_hash


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    object_store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    object_store.init()
    return object_store


def test_validate_hash_normalizes_and_accepts_valid_hash():
    digest = sha256(b"x").hexdigest()
    assert validate_hash(digest.upper()) == digest


@pytest.mark.parametrize(
    "bad_hash,match",
    [
        ("short", "Invalid SHA-256 hash length"),
        ("g" * 64, "Invalid SHA-256 hash"),
        (123, "SHA-256 hash must be a string"),
    ],
)
def test_validate_hash_rejects_invalid_hashes(bad_hash, match):
    with pytest.raises(StoreError, match=match):
        validate_hash(bad_hash)


def test_put_bytes_stores_and_deduplicates(store: ObjectStore):
    first = store.put_bytes(b"same")
    second = store.put_bytes(b"same")
    assert isinstance(first, BlobInfo)
    assert first.stored_new_blob is True
    assert second.stored_new_blob is False
    assert first.hash_hex == second.hash_hex
    assert store.get_path(first.hash_hex).parent.name == first.hash_hex[:2]
    assert store.verify_blob(first.hash_hex)


def test_put_file_streams_and_deduplicates(store: ObjectStore, tmp_path: Path):
    path = tmp_path / "file.bin"
    path.write_bytes(b"payload")
    first = store.put_file(path)
    second = store.put_file(path)
    assert first.stored_new_blob is True
    assert second.stored_new_blob is False
    assert first.hash_hex == sha256(b"payload").hexdigest()


def test_open_blob_requires_binary_mode(store: ObjectStore):
    blob = store.put_bytes(b"x")
    with pytest.raises(StoreError, match="binary mode"):
        store.open_blob(blob.hash_hex, "r")


def test_verify_blob_missing_raises(store: ObjectStore):
    missing = sha256(b"missing").hexdigest()
    with pytest.raises(IntegrityError, match="Missing blob"):
        store.verify_blob(missing)


def test_verify_blob_detects_corruption(store: ObjectStore):
    blob = store.put_bytes(b"good")
    store.get_path(blob.hash_hex).write_text("bad", encoding="utf-8")
    assert store.verify_blob(blob.hash_hex) is False


def test_iter_hashes_and_malformed_paths(store: ObjectStore, tmp_path: Path):
    blob = store.put_bytes(b"ok")
    hashes = store.iter_hashes()
    assert hashes == [blob.hash_hex]

    wrong_dir = store.objects_dir / "zz" / "not-a-hash"
    wrong_dir.parent.mkdir(parents=True, exist_ok=True)
    wrong_dir.write_bytes(b"x")
    malformed = store.iter_malformed_paths()
    assert wrong_dir in malformed


def test_put_file_missing_source_raises(store: ObjectStore, tmp_path: Path):
    with pytest.raises(StoreError, match="Could not store"):
        store.put_file(tmp_path / "missing.bin")


def test_put_bytes_repairs_corrupt_blob(store: ObjectStore):
    blob = store.put_bytes(b"good")
    store.get_path(blob.hash_hex).write_text("bad", encoding="utf-8")
    repaired = store.put_bytes(b"good")
    assert repaired.stored_new_blob is True
    assert store.verify_blob(blob.hash_hex)


def test_put_file_repairs_corrupt_blob(store: ObjectStore, tmp_path: Path):
    path = tmp_path / "file.bin"
    path.write_bytes(b"payload")
    first = store.put_file(path)
    store.get_path(first.hash_hex).write_text("bad", encoding="utf-8")
    second = store.put_file(path)
    assert second.stored_new_blob is True
    assert store.verify_blob(first.hash_hex)


def test_exists_and_get_path(store: ObjectStore):
    blob = store.put_bytes(b"data")
    assert store.exists(blob.hash_hex)
    assert store.get_path(blob.hash_hex).is_file()
    assert store.exists(sha256(b"other").hexdigest()) is False


def test_has_valid_blob_returns_false_when_blob_unreadable(store: ObjectStore, monkeypatch):
    from backup_tool.errors import StoreError

    blob = store.put_bytes(b"payload")

    def fail_verify(_hash_hex: str) -> bool:
        raise StoreError("Could not read blob")

    monkeypatch.setattr(store, "verify_blob", fail_verify)
    assert store.has_valid_blob(blob.hash_hex) is False


def test_has_valid_blob_rejects_corrupt_existing_file(store: ObjectStore):
    blob = store.put_bytes(b"payload")
    path = store.get_path(blob.hash_hex)
    path.write_text("corrupt", encoding="utf-8")
    assert path.is_file()
    assert store.exists(blob.hash_hex) is False
    assert store.has_valid_blob(blob.hash_hex) is False


def test_verify_blob_converts_hash_error_to_store_error(store: ObjectStore, monkeypatch):
    from backup_tool.errors import HashError

    blob = store.put_bytes(b"data")

    def fail_hash(_path, chunk_size=1024 * 1024):
        raise HashError("read failed")

    monkeypatch.setattr("backup_tool.object_store.hash_file", fail_hash)
    with pytest.raises(StoreError, match="Could not read blob"):
        store.verify_blob(blob.hash_hex)


def test_iter_blob_paths_ignores_symlinks(store: ObjectStore, tmp_path: Path):
    import os

    if not hasattr(os, "symlink"):
        pytest.skip("symlinks unavailable")

    try:
        blob = store.put_bytes(b"payload")
        blob_path = store.get_path(blob.hash_hex)
        blob_path.unlink()
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"outside")
        os.symlink(outside, blob_path)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform")

    assert blob.hash_hex not in store.iter_hashes()
    assert store.has_valid_blob(blob.hash_hex) is False
    assert blob_path in store.iter_malformed_paths()
