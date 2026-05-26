"""Tests for garbage collection and repository hygiene."""

from __future__ import annotations

import io
import os
import time
from contextlib import redirect_stderr
from hashlib import sha256
from pathlib import Path

import pytest

from backup_tool.chunking import verify_file_entry
from backup_tool.cli import main
from backup_tool.errors import IntegrityError
from backup_tool.manifest import FileEntry
from backup_tool.object_store import DEFAULT_TMP_MAX_AGE_SECONDS, ObjectStore
from backup_tool.repository import Repository
from tests.conftest import manifest_hash


def _place_misplaced_blob(store: ObjectStore, payload: bytes) -> tuple[str, Path]:
    hash_hex = sha256(payload).hexdigest()
    wrong_path = store.objects_dir / "zz" / hash_hex
    wrong_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_path.write_bytes(payload)
    return hash_hex, wrong_path


def test_gc_aggressive_quarantines_misplaced_blob(repo: Repository):
    hash_hex, wrong_path = _place_misplaced_blob(repo.object_store, b"misplaced")
    result = repo.gc(aggressive=True)
    assert not wrong_path.exists()
    assert any(hash_hex in item for item in result.quarantined_malformed)
    quarantine = repo.tmp_dir / "quarantine"
    assert any(path.is_file() for path in quarantine.iterdir())


def test_check_repair_quarantines_misplaced_blob(repo: Repository):
    hash_hex, wrong_path = _place_misplaced_blob(repo.object_store, b"misplaced")
    result = repo.check(repair=True)
    assert not wrong_path.exists()
    assert any(hash_hex in item for item in result.quarantined_malformed)
    assert result.repaired is True


def test_check_warns_about_stale_tmp(repo: Repository, monkeypatch):
    stale = repo.tmp_dir / ".blob.stale.tmp"
    stale.write_bytes(b"leftover")
    old = time.time() - DEFAULT_TMP_MAX_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    result = repo.check()
    assert any("stale blob tmp file" in warning for warning in result.warnings)
    assert stale.exists()


def test_gc_aggressive_removes_stale_manifest_and_lock_tmp(repo: Repository):
    stale_manifest = repo.snapshots_dir / ".2026-01-01T00-00-00-000000Z_abcd1234..json.tmp"
    stale_manifest.write_text("leftover", encoding="utf-8")
    stale_lock = repo.path / ".lock.stale.tmp"
    stale_lock.write_text("leftover", encoding="utf-8")
    old = time.time() - DEFAULT_TMP_MAX_AGE_SECONDS - 60
    os.utime(stale_manifest, (old, old))
    os.utime(stale_lock, (old, old))

    result = repo.gc(aggressive=True)
    assert not stale_manifest.exists()
    assert not stale_lock.exists()
    assert str(stale_manifest) in result.removed_tmp_files
    assert str(stale_lock) in result.removed_tmp_files


def test_check_repair_without_malformed_does_not_mark_repaired(repo: Repository):
    result = repo.check(repair=True)
    assert result.repaired is False


def test_quarantine_malformed_uses_unique_names(tmp_path: Path):
    store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    store.init()
    first = store.objects_dir / "aa" / "same-name"
    second = store.objects_dir / "bb" / "same-name"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    quarantine = tmp_path / "quarantine"
    moved = store.quarantine_malformed(quarantine, dry_run=False)
    assert len(moved) == 2
    destinations = list(quarantine.iterdir())
    assert len(destinations) == 2
    assert destinations[0].name != destinations[1].name


def test_gc_removes_stale_tmp(repo: Repository):
    stale = repo.tmp_dir / ".blob.stale.tmp"
    stale.write_bytes(b"leftover")
    old = time.time() - DEFAULT_TMP_MAX_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    result = repo.gc()
    assert not stale.exists()
    assert str(stale) in result.removed_tmp_files
    assert result.tmp_bytes_deleted == len(b"leftover")


def test_verify_distinguishes_missing_and_mismatch(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    entry = repo.manifest_store.latest().files["a.txt"]
    blob_hash = entry.chunks[0] if entry.chunks else entry.hash

    missing_entry = FileEntry(type="file", hash=manifest_hash("missing"))
    with pytest.raises(IntegrityError, match="Missing blob"):
        verify_file_entry(repo.object_store, missing_entry)

    repo.object_store.get_path(blob_hash).write_text("corrupt", encoding="utf-8")
    with pytest.raises(IntegrityError, match="Hash mismatch"):
        verify_file_entry(repo.object_store, entry)


def test_verify_reports_unreadable_blob(repo: Repository, source_dir: Path, monkeypatch):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)

    def fail_open(_self, _hash_hex, mode="rb"):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(repo.object_store.__class__, "open_blob", fail_open)
    result = repo.verify("latest")
    assert result.ok is False
    assert any("Could not read blob" in error for error in result.errors)


def test_cli_verify_unreadable_blob_exit_code(repo: Repository, source_dir: Path, repo_path: Path, monkeypatch):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)

    def fail_open(_self, _hash_hex, mode="rb"):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(repo.object_store.__class__, "open_blob", fail_open)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["verify", "latest", "--repo", str(repo_path)])
    assert code == 2
    assert "Could not read blob" in stderr.getvalue()


def test_prune_fsyncs_snapshots_dir(repo: Repository, source_dir: Path, monkeypatch):
    (source_dir / "a.txt").write_text("one", encoding="utf-8")
    repo.backup(source_dir)
    (source_dir / "b.txt").write_text("two", encoding="utf-8")
    repo.backup(source_dir)

    fsync_calls: list[Path] = []
    monkeypatch.setattr(
        "backup_tool.repository.fsync_directory",
        lambda path: fsync_calls.append(path),
    )
    repo.prune(keep=1)
    assert repo.snapshots_dir in fsync_calls
