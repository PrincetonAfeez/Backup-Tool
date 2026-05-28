"""Focused contract tests for validation and repository hygiene."""

from __future__ import annotations

import io
import os
import time
from contextlib import redirect_stderr
from pathlib import Path

import pytest

from backup_tool.cli import build_parser, main
from backup_tool.errors import HashError, LockError, ManifestError, RepositoryError, StoreError
from backup_tool.lock import (
    DEFAULT_LOCK_STALE_SECONDS,
    RepositoryLock,
    read_lock_pid,
    read_lock_token,
)
from backup_tool.verify import check_repository
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


def test_check_repair_quarantines_orphan_manifest_json(
    repo: Repository,
    source_dir: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    orphan = repo.snapshots_dir / "orphan.json"
    orphan.write_text("{}", encoding="utf-8")

    result = repo.check(repair=True)

    assert result.ok is True
    assert result.repaired is True
    assert not orphan.exists()
    assert len(result.quarantined_manifests) == 1
    assert any("Quarantined unloadable manifest: orphan.json" in warning for warning in result.warnings)
    assert repo.list_snapshots()


def test_check_repair_migrates_legacy_manifest_missing_sidecar(
    repo: Repository,
    source_dir: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    manifest_path = repo.manifest_store.path_for(repo.manifest_store.latest().snapshot_id)
    sidecar = manifest_path.with_name(f"{manifest_path.name}.sha256")
    sidecar.unlink()

    result = repo.check(repair=True)

    assert result.ok is True
    assert result.repaired is True
    assert sidecar.exists()
    assert any("Migrated manifest digest sidecar" in warning for warning in result.warnings)


def test_check_repair_help_describes_full_hygiene_actions():
    parser = build_parser()
    check_parser = parser._subparsers._group_actions[0].choices["check"]
    repair_action = next(action for action in check_parser._actions if action.dest == "repair")
    help_text = repair_action.help or ""

    assert "migrate missing manifest digests" in help_text
    assert "quarantine malformed object paths" in help_text
    assert "unloadable snapshot manifests" in help_text
    assert "stale tmp artifacts" in help_text
    assert "orphan staging" in help_text


def test_object_store_exists_requires_valid_or_staged_blob(store: ObjectStore):
    blob = store.put_bytes(b"payload")
    assert store.exists(blob.hash_hex)

    path = store.get_path(blob.hash_hex)
    path.write_text("corrupt", encoding="utf-8")
    assert path.is_file()
    assert store.exists(blob.hash_hex) is False
    assert store.has_valid_blob(blob.hash_hex) is False


def test_backup_keeps_repaired_blob_when_post_verify_fails(
    repo: Repository,
    source_dir: Path,
    monkeypatch,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    first = repo.backup(source_dir)
    blob_hash = first.manifest.files["a.txt"].hash
    assert blob_hash is not None
    repo.object_store.get_path(blob_hash).write_text("corrupt", encoding="utf-8")

    captured_promotions: list = []
    real_build_snapshot = repo.engine.build_snapshot

    def capture_build_snapshot(*args, **kwargs):
        result = real_build_snapshot(*args, **kwargs)
        captured_promotions.append(result.promotion)
        return result

    def fail_verify(_manifest):
        raise RepositoryError("Manifest references invalid blobs: a.txt: bad")

    monkeypatch.setattr(repo.engine, "build_snapshot", capture_build_snapshot)
    monkeypatch.setattr(repo, "_ensure_manifest_blobs_exist", fail_verify)

    with pytest.raises(RepositoryError, match="invalid blobs"):
        repo.backup(source_dir)

    assert captured_promotions
    assert blob_hash in captured_promotions[-1].repaired_blobs
    assert blob_hash not in captured_promotions[-1].new_blobs
    assert repo.object_store.has_valid_blob(blob_hash)


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


def test_invalid_utf8_repo_json_raises_on_repository_open(repo_path: Path):
    Repository.init(repo_path)
    repo = Repository(repo_path)
    repo.repo_json.write_bytes(b"\xff")

    with pytest.raises(RepositoryError, match="Invalid repo.json"):
        repo.list_snapshots()


def test_invalid_utf8_repo_json_check_reports_error(repo: Repository):
    repo.repo_json.write_bytes(b"\xff")

    result = repo.check()

    assert result.ok is False
    assert any("Invalid repo.json" in error for error in result.errors)


def test_invalid_utf8_repo_json_check_repository_reports_error(repo: Repository):
    repo.repo_json.write_bytes(b"\xff")

    result = check_repository(repo)

    assert result.ok is False
    assert any("Invalid repo.json" in error for error in result.errors)


def test_invalid_utf8_repo_json_cli_does_not_return_internal_error(
    repo_path: Path,
    source_dir: Path,
):
    Repository.init(repo_path)
    repo_path.joinpath("repo.json").write_bytes(b"\xff")
    stderr = io.StringIO()

    with redirect_stderr(stderr):
        backup_code = main(["backup", str(source_dir), "--repo", str(repo_path)])
        check_code = main(["check", "--repo", str(repo_path)])

    assert backup_code == 1
    assert check_code == 2
    assert backup_code != 4
    assert check_code != 4
    stderr_text = stderr.getvalue()
    assert "Invalid repo.json" in stderr_text
    assert "internal error" not in stderr_text.lower()


def test_invalid_utf8_lock_file_returns_none_from_readers(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_bytes(b"\xff")

    assert read_lock_pid(lock_path) is None
    assert read_lock_token(lock_path) is None


def test_repository_lock_acquires_after_old_non_utf8_lock(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_bytes(b"\xff")
    old = time.time() - DEFAULT_LOCK_STALE_SECONDS - 60
    os.utime(lock_path, (old, old))

    with RepositoryLock(lock_path):
        assert read_lock_pid(lock_path) == os.getpid()


def test_repository_lock_rejects_recent_non_utf8_lock(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_bytes(b"\xff")

    with pytest.raises(LockError, match="Repository is locked"):
        RepositoryLock(lock_path).acquire()


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
