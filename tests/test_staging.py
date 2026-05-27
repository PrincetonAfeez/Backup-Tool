"""Tests for blob transaction staging."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from backup_tool.object_store import ObjectStore, PromotionResult
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
    store.promote_staging(TEST_SNAPSHOT_ID, allowed_hashes={blob_hash})
    assert store.get_path(blob_hash).exists()
    assert store.verify_blob(blob_hash)


def test_promote_staging_without_filter_promotes_all_staged_blobs(store: ObjectStore):
    first_hash = sha256(b"one").hexdigest()
    second_hash = sha256(b"two").hexdigest()
    store.begin_staging(TEST_SNAPSHOT_ID)
    store.put_bytes(b"one")
    store.put_bytes(b"two")
    store.promote_staging(TEST_SNAPSHOT_ID)
    assert store.get_path(first_hash).exists()
    assert store.get_path(second_hash).exists()


def test_promote_staging_classifies_new_and_repaired_blobs(store: ObjectStore):
    blob_hash = sha256(b"payload").hexdigest()
    store.put_bytes(b"payload")
    path = store.get_path(blob_hash)
    path.write_text("corrupt", encoding="utf-8")

    store.begin_staging(TEST_SNAPSHOT_ID)
    store.put_bytes(b"payload")
    result = store.promote_staging(TEST_SNAPSHOT_ID, allowed_hashes={blob_hash})

    assert isinstance(result, PromotionResult)
    assert blob_hash in result.repaired_blobs
    assert blob_hash not in result.new_blobs
    assert store.has_valid_blob(blob_hash)


def test_promote_staging_classifies_new_blob_without_existing_final(store: ObjectStore):
    blob_hash = sha256(b"fresh").hexdigest()
    store.begin_staging(TEST_SNAPSHOT_ID)
    store.put_bytes(b"fresh")
    result = store.promote_staging(TEST_SNAPSHOT_ID, allowed_hashes={blob_hash})

    assert blob_hash in result.new_blobs
    assert blob_hash not in result.repaired_blobs


def test_promote_staging_skips_unreferenced_blobs(store: ObjectStore):
    keep_hash = sha256(b"keep").hexdigest()
    orphan_hash = sha256(b"orphan").hexdigest()
    store.begin_staging(TEST_SNAPSHOT_ID)
    store.put_bytes(b"keep")
    store.put_bytes(b"orphan")
    store.promote_staging(TEST_SNAPSHOT_ID, allowed_hashes={keep_hash})
    assert store.get_path(keep_hash).exists()
    assert not store.get_path(orphan_hash).exists()


def test_strict_backup_leaves_no_orphan_blobs(repo: Repository, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    result = repo.backup(source_dir, strict=True, skip_predicate=skip_skip_me)
    assert result.manifest is None
    assert repo.object_store.iter_hashes() == []
