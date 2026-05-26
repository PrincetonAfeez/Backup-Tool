"""Tests for blob transaction staging."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from backup_tool.object_store import ObjectStore
from backup_tool.repository import Repository
from tests.conftest import TEST_SNAPSHOT_ID, skip_skip_me


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    object_store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    object_store.init()
    return object_store


def test_staging_discarded_on_abort(store: ObjectStore):
    blob_hash = sha256(b"payload").hexdigest()
    store.begin_staging(TEST_SNAPSHOT_ID)
    store.put_bytes(b"payload")
    assert store.has_staged_blob(blob_hash)
    store.discard_staging(TEST_SNAPSHOT_ID)
    assert not store.has_staged_blob(blob_hash)
    assert not store.get_path(blob_hash).exists()


def test_staging_promoted_to_objects(store: ObjectStore):
    blob_hash = sha256(b"payload").hexdigest()
    store.begin_staging(TEST_SNAPSHOT_ID)
    store.put_bytes(b"payload")
    store.promote_staging(TEST_SNAPSHOT_ID)
    assert store.get_path(blob_hash).exists()
    assert store.verify_blob(blob_hash)


def test_strict_backup_leaves_no_orphan_blobs(repo: Repository, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    result = repo.backup(source_dir, strict=True, skip_predicate=skip_skip_me)
    assert result.manifest is None
    assert repo.object_store.iter_hashes() == []
