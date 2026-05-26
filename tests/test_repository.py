"""Tests for backup_tool.repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backup_tool.errors import LockError, RepositoryError, RestoreError
from backup_tool.lock import RepositoryLock
from backup_tool.repository import Repository
from backup_tool.verify import check_repository


def test_init_creates_repository(repo_path: Path):
    repo = Repository.init(repo_path)
    assert repo.repo_json.exists()
    metadata = json.loads(repo.repo_json.read_text(encoding="utf-8"))
    assert metadata["chunking"] == "fixed-1mb-blocks-above-threshold"


def test_init_existing_repository_raises(repo_path: Path):
    Repository.init(repo_path)
    with pytest.raises(RepositoryError, match="already exists"):
        Repository.init(repo_path)


def test_init_nonempty_directory_raises(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "unrelated.txt").write_text("data", encoding="utf-8")
    with pytest.raises(RepositoryError, match="not empty"):
        Repository.init(repo_path)


def test_init_nonempty_directory_with_allow_nonempty(tmp_path: Path):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "unrelated.txt").write_text("data", encoding="utf-8")
    repo = Repository.init(repo_path, allow_nonempty=True)
    assert repo.repo_json.exists()
    assert (repo_path / "unrelated.txt").exists()


def test_invalid_repo_metadata_rejected_on_open(repo_path: Path):
    Repository.init(repo_path)
    repo_path.joinpath("repo.json").write_text(
        json.dumps(
            {
                "version": 1,
                "created_at": "2026-01-01T00:00:00.000000Z",
                "hash_algorithm": "sha256",
                "storage": "wrong",
                "object_layout": "sha256-prefix-2",
                "chunking": "fixed-1mb-blocks-above-threshold",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RepositoryError, match="Unsupported storage"):
        Repository(repo_path).list_snapshots()


def test_check_rejects_invalid_repo_metadata(repo: Repository):
    metadata = json.loads(repo.repo_json.read_text(encoding="utf-8"))
    metadata["chunking"] = "rolling-hash"
    repo.repo_json.write_text(json.dumps(metadata), encoding="utf-8")
    check = check_repository(repo)
    assert check.ok is False
    assert any("chunking" in error for error in check.errors)


def test_backup_on_uninitialized_path_raises(tmp_path: Path, source_dir: Path):
    with pytest.raises(RepositoryError, match="Not a backup repository"):
        Repository(tmp_path / "missing").backup(source_dir)


def test_full_workflow(repo: Repository, populated_source: Path, tmp_path: Path):
    restore_path = tmp_path / "restore"
    first = repo.backup(populated_source)
    assert first.committed
    assert first.manifest.status == "complete"

    (populated_source / "notes" / "todo.txt").write_text("two\n", encoding="utf-8")
    (populated_source / "new.txt").write_text("new\n", encoding="utf-8")
    (populated_source / "same-b.txt").unlink()
    second = repo.backup(populated_source)

    assert "new.txt" in second.diff.added
    assert "notes/todo.txt" in second.diff.changed
    assert "same-b.txt" in second.diff.deleted

    summaries = repo.list_snapshots()
    diff = repo.diff(summaries[0].snapshot_id, summaries[1].snapshot_id)
    assert diff.added == second.diff.added

    assert repo.verify("latest").ok
    check = repo.check()
    assert check.ok
    assert check.snapshot_count == 2

    restore = repo.restore("latest", restore_path)
    assert restore.restored_files == 3

    with pytest.raises(RestoreError):
        repo.restore("latest", restore_path)

    prune = repo.prune(keep=1, dry_run=True)
    assert len(prune.deleted_snapshots) == 1
    assert len(repo.list_snapshots()) == 2

    repo.prune(keep=1)
    assert len(repo.list_snapshots()) == 1
    assert repo.gc(dry_run=True).deleted_blobs


def test_dry_run_does_not_commit(repo: Repository, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    result = repo.backup(source_dir, dry_run=True)
    assert result.dry_run
    assert not result.committed
    assert repo.list_snapshots() == []


def test_repo_inside_source_is_excluded(tmp_path: Path):
    source = tmp_path / "source"
    repo_path = source / ".mybackup"
    source.mkdir()
    (source / "data.txt").write_text("data", encoding="utf-8")
    Repository.init(repo_path)
    result = Repository(repo_path).backup(source)
    assert sorted(result.manifest.files) == ["data.txt"]
    assert any("added to --exclude automatically" in warning for warning in result.warnings)


def test_empty_source_backup(repo: Repository, source_dir: Path):
    result = repo.backup(source_dir)
    assert result.committed
    assert result.manifest.stats["entry_count"] == 0
    assert repo.verify("latest").ok


def test_unchanged_second_backup(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("same", encoding="utf-8")
    repo.backup(source_dir)
    second = repo.backup(source_dir)
    assert second.manifest.stats["new_bytes_stored"] == 0
    assert second.manifest.stats["unchanged_files"] == 1
    assert second.diff.unchanged == ["a.txt"]


def test_resolve_snapshot_latest_and_json_suffix(repo_with_snapshot: Repository):
    latest = repo_with_snapshot.manifest_store.latest()
    assert repo_with_snapshot._resolve_snapshot("latest") == latest
    loaded = repo_with_snapshot._resolve_snapshot(f"{latest.snapshot_id}.json")
    assert loaded.snapshot_id == latest.snapshot_id


def test_resolve_snapshot_missing_raises(repo: Repository):
    with pytest.raises(RepositoryError, match="No snapshots found"):
        repo._resolve_snapshot("latest")


def test_verify_detects_corrupt_blob(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    entry = next(iter(repo.manifest_store.latest().files.values()))
    blob_hash = entry.chunks[0] if entry.chunks else entry.hash
    repo.object_store.get_path(blob_hash).write_text("corrupt", encoding="utf-8")
    verify = repo.verify("latest")
    assert verify.ok is False
    assert any("Hash mismatch" in error for error in verify.errors)


def test_check_invalid_manifest_and_repo_json(repo: Repository):
    (repo.snapshots_dir / "bad.json").write_text("{bad", encoding="utf-8")
    repo.repo_json.write_text("{bad", encoding="utf-8")
    check = repo.check()
    assert check.ok is False


def test_prune_negative_keep_raises(repo: Repository):
    with pytest.raises(RepositoryError, match="keep must be >= 0"):
        repo.prune(-1)


def test_prune_with_gc(repo: Repository, source_dir: Path):
    (source_dir / "v1.txt").write_text("one", encoding="utf-8")
    first = repo.backup(source_dir)
    old_hash = first.manifest.files["v1.txt"].hash
    (source_dir / "v1.txt").write_text("two", encoding="utf-8")
    repo.backup(source_dir)
    result = repo.prune(keep=1, run_gc=True)
    assert result.gc_result is not None
    assert not repo.object_store.exists(old_hash)


def test_prune_dry_run_gc_uses_kept_manifests(repo: Repository, source_dir: Path):
    (source_dir / "v1.txt").write_text("one", encoding="utf-8")
    first = repo.backup(source_dir)
    old_hash = first.manifest.files["v1.txt"].hash
    (source_dir / "v1.txt").write_text("two", encoding="utf-8")
    repo.backup(source_dir)
    result = repo.prune(keep=1, dry_run=True, run_gc=True)
    assert result.gc_result is not None
    assert old_hash in result.gc_result.deleted_blobs
    assert repo.object_store.exists(old_hash)


def test_gc_dry_run_reports_bytes_deleted(repo: Repository):
    orphan = repo.object_store.put_bytes(b"orphan-bytes")
    result = repo.gc(dry_run=True)
    assert orphan.hash_hex in result.deleted_blobs
    assert result.bytes_deleted == len(b"orphan-bytes")
    assert repo.object_store.exists(orphan.hash_hex)


def test_active_lock_blocks_backup(repo: Repository, source_dir: Path):
    (source_dir / "data.txt").write_text("data", encoding="utf-8")
    lock = RepositoryLock(repo.lock_path)
    lock.acquire()
    try:
        with pytest.raises(LockError):
            repo.backup(source_dir)
    finally:
        lock.release()


def test_stale_lock_auto_cleared(repo: Repository, source_dir: Path):
    (source_dir / "data.txt").write_text("data", encoding="utf-8")
    repo.lock_path.write_text("pid=0\ntime=0\n", encoding="utf-8")
    result = repo.backup(source_dir)
    assert result.committed


def test_dry_run_backup_acquires_lock(repo: Repository, source_dir: Path):
    (source_dir / "data.txt").write_text("data", encoding="utf-8")
    lock = RepositoryLock(repo.lock_path)
    lock.acquire()
    try:
        with pytest.raises(LockError):
            repo.backup(source_dir, dry_run=True)
    finally:
        lock.release()


def test_verify_acquires_lock(repo: Repository, source_dir: Path):
    (source_dir / "data.txt").write_text("data", encoding="utf-8")
    repo.backup(source_dir)
    lock = RepositoryLock(repo.lock_path)
    lock.acquire()
    try:
        with pytest.raises(LockError):
            repo.verify("latest")
    finally:
        lock.release()


def test_list_snapshots_acquires_lock(repo: Repository, source_dir: Path):
    (source_dir / "data.txt").write_text("data", encoding="utf-8")
    repo.backup(source_dir)
    lock = RepositoryLock(repo.lock_path)
    lock.acquire()
    try:
        with pytest.raises(LockError):
            repo.list_snapshots()
    finally:
        lock.release()
