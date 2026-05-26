"""Tests for documented backlog gaps (U-Test-1–U-Test-10)."""

from __future__ import annotations

import io
import os
import stat
from contextlib import redirect_stderr
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import pytest

from backup_tool.cli import main
from backup_tool.errors import LockError, RepositoryError
from backup_tool.lock import RepositoryLock, read_lock_token
from backup_tool.repository import Repository
from backup_tool.snapshot_engine import SnapshotEngine


def _skip_skip_me(_path: Path, manifest_path: str) -> str | None:
    if manifest_path == "skip-me.txt":
        return "skipped by test predicate"
    return None


def test_backup_repairs_corrupt_existing_blob(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    first = repo.backup(source_dir)
    blob_hash = first.manifest.files["a.txt"].hash
    assert blob_hash is not None
    repo.object_store.get_path(blob_hash).write_text("corrupt", encoding="utf-8")
    assert repo.object_store.verify_blob(blob_hash) is False

    repo.backup(source_dir)
    assert repo.object_store.verify_blob(blob_hash)


def test_walk_source_skips_special_file_type(engine: SnapshotEngine, source_dir: Path):
    special = source_dir / "special"
    special.write_text("x", encoding="utf-8")
    with patch.object(Path, "is_symlink", return_value=False):
        with patch.object(Path, "lstat") as mock_lstat:
            mock_lstat.return_value = os.stat_result(
                (stat.S_IFIFO | 0o644, 0, 0, 0, 0, 0, 1, 0, 0, 0),
            )
            result = engine.build_snapshot(source_dir, None)
    assert result.manifest.status == "partial"
    assert any(item.path == "special" and "unsupported file type" in item.reason for item in result.skipped)


def test_prune_dry_run_gc_matches_live_gc_blob_count(repo: Repository, source_dir: Path):
    (source_dir / "v1.txt").write_text("one", encoding="utf-8")
    repo.backup(source_dir)
    (source_dir / "v1.txt").write_text("two", encoding="utf-8")
    repo.backup(source_dir)

    dry = repo.prune(keep=1, dry_run=True, run_gc=True)
    live = repo.prune(keep=1, dry_run=False, run_gc=True)
    assert dry.gc_result is not None
    assert live.gc_result is not None
    assert len(dry.gc_result.deleted_blobs) == len(live.gc_result.deleted_blobs)
    assert dry.gc_result.bytes_deleted == live.gc_result.bytes_deleted


def test_backup_rejects_source_equal_to_repo(repo: Repository, repo_path: Path):
    with pytest.raises(RepositoryError, match="must not be the repository directory"):
        repo.backup(repo_path)


def test_backup_rejects_source_inside_repo(tmp_path: Path):
    repo_path = tmp_path / "repo"
    source = repo_path / "nested" / "source"
    source.mkdir(parents=True)
    (source / "data.txt").write_text("data", encoding="utf-8")
    Repository.init(repo_path, allow_nonempty=True)
    with pytest.raises(RepositoryError, match="must not be inside the repository"):
        Repository(repo_path).backup(source)


@pytest.mark.parametrize("payload", ["", "time=0\n"])
def test_repository_lock_partial_or_empty_file_raises(tmp_path: Path, payload: str):
    lock_path = tmp_path / "lock"
    lock_path.write_text(payload, encoding="utf-8")
    with pytest.raises(LockError, match="Repository is locked"):
        RepositoryLock(lock_path).acquire()


def test_cli_backup_rejects_invalid_exclude_pattern(repo_path: Path, source_dir: Path):
    Repository.init(repo_path)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(
            ["backup", str(source_dir), "--repo", str(repo_path), "--exclude", "../secret"]
        )
    assert exit_code == 1
    assert "Unsafe exclude pattern" in stderr.getvalue()
    assert "Unsafe manifest path" not in stderr.getvalue()


def test_cli_backup_rejects_dotdot_exclude_with_pattern_in_message(repo_path: Path, source_dir: Path):
    Repository.init(repo_path)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(
            ["backup", str(source_dir), "--repo", str(repo_path), "--exclude", ".."]
        )
    assert exit_code == 1
    assert "Unsafe exclude pattern" in stderr.getvalue()


def test_gc_reclaims_orphan_blob_from_strict_abort(repo: Repository, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    keep_hash = sha256(b"keep").hexdigest()

    result = repo.backup(source_dir, strict=True, skip_predicate=_skip_skip_me)
    assert result.manifest is None
    assert not repo.object_store.exists(keep_hash)
    assert list(repo.object_store.iter_hashes()) == []


def test_break_lock_release_does_not_steal_replaced_lock(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_text(f"pid={os.getpid()}\ntime=0\n", encoding="utf-8")

    first = RepositoryLock(lock_path, break_lock=True)
    first.acquire()
    try:
        if first._fd is not None:
            os.close(first._fd)
            first._fd = None
        lock_path.unlink(missing_ok=True)

        second = RepositoryLock(lock_path)
        second.acquire()
        try:
            first.release()
            assert lock_path.exists()
            assert read_lock_token(lock_path) == second._token
        finally:
            second.release()
    finally:
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)
