"""Focused contract tests for validation and repository hygiene."""

from __future__ import annotations

from pathlib import Path

import pytest

from backup_tool.errors import HashError, ManifestError, RepositoryError, StoreError
from backup_tool.manifest import (
    FileEntry,
    Manifest,
    ManifestStore,
    verify_manifest_digest,
    validate_manifest_version,
    write_manifest_digest,
)
from backup_tool.paths import validate_exclude_pattern
from backup_tool.object_store import ObjectStore, validate_hash
from backup_tool.repo_metadata import validate_repo_version
from backup_tool.staging import validate_created_at, validate_snapshot_id, validate_staging_snapshot_id
from backup_tool.repository import Repository
from tests.conftest import TEST_CREATED_AT, TEST_SNAPSHOT_ID, manifest_hash


@pytest.fixture
def store(tmp_path: Path) -> ObjectStore:
    object_store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    object_store.init()
    return object_store


def test_validate_hash_rejects_non_string_with_store_error():
    with pytest.raises(StoreError, match="SHA-256 hash must be a string"):
        validate_hash(123)  # type: ignore[arg-type]


def test_file_entry_direct_construct_rejects_non_string_hash():
    with pytest.raises(ManifestError, match="SHA-256 hash must be a string"):
        FileEntry(type="file", hash=123, size=1)  # type: ignore[arg-type]


def test_file_entry_direct_construct_rejects_non_string_chunk_hash():
    with pytest.raises(ManifestError, match="Invalid file entry chunks\\[0\\]"):
        FileEntry(
            type="file",
            hash=manifest_hash("whole"),
            size=1,
            chunks=(123,),  # type: ignore[arg-type]
        )


def test_validate_snapshot_id_rejects_non_string():
    with pytest.raises(ManifestError, match="Snapshot id must be a string"):
        validate_snapshot_id(123)  # type: ignore[arg-type]


def test_validate_created_at_rejects_non_string():
    with pytest.raises(ManifestError, match="Manifest created_at must be a string"):
        validate_created_at(123)  # type: ignore[arg-type]


def test_manifest_direct_construct_rejects_non_string_file_keys():
    entry = FileEntry(type="file", hash=manifest_hash("x"), size=1)
    with pytest.raises(ManifestError, match="file path must be a string or Path"):
        Manifest(
            snapshot_id=TEST_SNAPSHOT_ID,
            created_at=TEST_CREATED_AT,
            source="src",
            status="complete",
            stats={"entry_count": 1, "regular_file_count": 1},
            files={123: entry},  # type: ignore[dict-item]
        )


def test_object_store_get_path_rejects_non_string_hash(store: ObjectStore):
    with pytest.raises(StoreError, match="SHA-256 hash must be a string"):
        store.get_path(123)  # type: ignore[arg-type]


def test_check_reports_digest_verification_errors_instead_of_hash_error(
    repo: Repository,
    source_dir: Path,
    monkeypatch,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)

    def fail_hash(_path: Path, chunk_size: int = 1024 * 1024):
        raise HashError("read failed")

    monkeypatch.setattr("backup_tool.manifest.hash_file", fail_hash)

    result = repo.check()

    assert result.ok is False
    assert any("Could not verify manifest digest" in error for error in result.errors)


def test_validate_manifest_version_rejects_boolean():
    with pytest.raises(ManifestError, match="Manifest version must be an integer"):
        validate_manifest_version(True)


def test_validate_repo_version_rejects_boolean():
    assert validate_repo_version(True) == ["Repository version must be an integer"]


def test_manifest_from_dict_rejects_non_object_root():
    with pytest.raises(ManifestError, match="Manifest root must be an object"):
        Manifest.from_dict([])  # type: ignore[arg-type]


def test_file_entry_from_dict_rejects_non_object_root():
    with pytest.raises(ManifestError, match="File entry must be an object"):
        FileEntry.from_dict("file")  # type: ignore[arg-type]


def test_validate_staging_snapshot_id_rejects_non_string():
    with pytest.raises(StoreError, match="must be a string"):
        validate_staging_snapshot_id(123)  # type: ignore[arg-type]


def test_check_repair_removes_orphan_manifest_digest_sidecar(
    repo: Repository,
    source_dir: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    orphan = repo.snapshots_dir / "orphan.json.sha256"
    orphan.write_text(f"{manifest_hash('orphan')}\n", encoding="utf-8")

    result = repo.check(repair=True)

    assert result.ok is True
    assert result.repaired is True
    assert not orphan.exists()
    assert any("Removed 1 orphan manifest digest sidecar" in warning for warning in result.warnings)


def test_backup_removes_promoted_blobs_when_post_verify_fails(
    repo: Repository,
    source_dir: Path,
    monkeypatch,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")

    def fail_verify(_manifest):
        raise RepositoryError("Manifest references invalid blobs: a.txt: bad")

    monkeypatch.setattr(repo, "_ensure_manifest_blobs_exist", fail_verify)

    with pytest.raises(RepositoryError, match="invalid blobs"):
        repo.backup(source_dir)

    assert repo.object_store.iter_hashes() == []


def test_manifest_from_dict_rejects_non_string_file_keys():
    with pytest.raises(ManifestError, match="file path must be a string or Path"):
        Manifest.from_dict(
            {
                "version": 1,
                "snapshot_id": TEST_SNAPSHOT_ID,
                "created_at": TEST_CREATED_AT,
                "source": "src",
                "hash_algorithm": "sha256",
                "status": "complete",
                "stats": {},
                "files": {123: {"type": "file", "hash": manifest_hash("x")}},
            }
        )


def test_manifest_from_dict_rejects_non_string_hash_algorithm():
    with pytest.raises(ManifestError, match="hash_algorithm must be a string"):
        Manifest.from_dict(
            {
                "version": 1,
                "snapshot_id": TEST_SNAPSHOT_ID,
                "created_at": TEST_CREATED_AT,
                "source": "src",
                "hash_algorithm": 123,
                "status": "complete",
                "stats": {},
                "files": {},
            }
        )


def test_manifest_from_dict_rejects_non_string_status():
    with pytest.raises(ManifestError, match="status must be a string"):
        Manifest.from_dict(
            {
                "version": 1,
                "snapshot_id": TEST_SNAPSHOT_ID,
                "created_at": TEST_CREATED_AT,
                "source": "src",
                "hash_algorithm": "sha256",
                "status": 123,
                "stats": {},
                "files": {},
            }
        )


def test_load_path_rejects_non_utf8_manifest(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    path = store.save(_sample_manifest())
    path.write_bytes(b"\xff\xfe not valid utf-8")
    write_manifest_digest(path)

    with pytest.raises(ManifestError, match="Could not load manifest"):
        store.load_path(path)


def test_verify_manifest_digest_rejects_non_utf8_sidecar(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    path = store.save(_sample_manifest())
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.write_bytes(b"\xff\xfe")

    with pytest.raises(ManifestError, match="Could not verify manifest digest"):
        verify_manifest_digest(path)


def _sample_manifest(**overrides) -> Manifest:
    data = {
        "snapshot_id": TEST_SNAPSHOT_ID,
        "created_at": TEST_CREATED_AT,
        "source": "source",
        "status": "complete",
        "stats": {"entry_count": 0},
        "files": {},
    }
    data.update(overrides)
    return Manifest(**data)


def test_migrate_missing_digests_skips_non_utf8_manifest(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    path = store.path_for(TEST_SNAPSHOT_ID)
    path.write_bytes(b"\xff\xfe")
    result = store.migrate_missing_digests()
    assert result.migrated == []
    assert any("Could not load manifest" in item for item in result.skipped)


def test_validate_exclude_pattern_rejects_non_string():
    with pytest.raises(ManifestError, match="Exclude pattern must be a string"):
        validate_exclude_pattern(123)  # type: ignore[arg-type]


def test_backup_cleanup_failure_does_not_mask_verify_error(
    repo: Repository,
    source_dir: Path,
    monkeypatch,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    blob_path = repo.object_store.get_path(manifest_hash("hello"))
    real_unlink = Path.unlink

    def fail_verify(_manifest):
        raise RepositoryError("Manifest references invalid blobs: a.txt: bad")

    def selective_unlink(self, missing_ok=False):
        if self == blob_path:
            raise OSError("permission denied")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(repo, "_ensure_manifest_blobs_exist", fail_verify)
    monkeypatch.setattr(Path, "unlink", selective_unlink)

    with pytest.raises(RepositoryError, match="invalid blobs"):
        repo.backup(source_dir)


def test_check_warns_on_orphan_manifest_digest_sidecar(
    repo: Repository,
    source_dir: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    orphan = repo.snapshots_dir / "orphan.json.sha256"
    orphan.write_text(f"{manifest_hash('orphan')}\n", encoding="utf-8")

    result = repo.check()

    assert result.ok is True
    assert any("orphan manifest digest sidecar" in warning for warning in result.warnings)
