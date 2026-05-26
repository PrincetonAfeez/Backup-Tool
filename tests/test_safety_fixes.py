"""Tests for safety and correctness fixes."""

from __future__ import annotations

import os
import stat
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import pytest

from backup_tool.chunking import verify_file_content
from backup_tool.diff import classify_entries
from backup_tool.errors import ManifestError, RepositoryError, RestoreError
from backup_tool.manifest import FileEntry
from backup_tool.object_store import ObjectStore
from backup_tool.paths import validate_exclude_pattern
from backup_tool.repository import Repository
from backup_tool.snapshot_engine import SnapshotEngine
from tests.conftest import manifest_hash, skip_skip_me


@pytest.fixture
def engine(tmp_path: Path) -> SnapshotEngine:
    store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    store.init()
    return SnapshotEngine(store)


def test_validate_exclude_pattern_allows_absolute_style():
    assert validate_exclude_pattern("/etc") == "/etc"
    assert validate_exclude_pattern("*.tmp") == "*.tmp"


def test_validate_exclude_pattern_rejects_unsafe():
    with pytest.raises(ManifestError, match="Unsafe exclude pattern"):
        validate_exclude_pattern("../secret")


def test_backup_rejects_repository_as_source(repo: Repository, repo_path: Path):
    with pytest.raises(RepositoryError, match="must not be the repository directory"):
        repo.backup(repo_path)


def test_backup_rejects_source_inside_repository(tmp_path: Path):
    repo_path = tmp_path / "repo"
    source = repo_path / "nested" / "source"
    source.mkdir(parents=True)
    (source / "data.txt").write_text("data", encoding="utf-8")
    Repository.init(repo_path, allow_nonempty=True)
    with pytest.raises(RepositoryError, match="must not be inside the repository"):
        Repository(repo_path).backup(source)


def test_init_existing_file_raises_repository_error(tmp_path: Path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RepositoryError, match="Cannot create repository"):
        Repository.init(blocker / "repo")


def test_classify_entries_detects_mode_only_change():
    shared_hash = manifest_hash("h")
    previous = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mode=0o644)}
    current = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mode=0o600)}
    result = classify_entries(current, previous)
    assert result.changed == ["a.txt"]
    assert result.unchanged == []


def test_build_snapshot_records_empty_directory(engine: SnapshotEngine, source_dir: Path):
    empty = source_dir / "empty"
    empty.mkdir()
    result = engine.build_snapshot(source_dir, None)
    assert "empty" in result.manifest.files
    assert result.manifest.files["empty"].type == "directory"


def test_restore_snapshot_recreates_empty_directory(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    empty = source_dir / "empty"
    empty.mkdir()
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    result = engine.restore_snapshot(manifest, destination)
    assert result.restored_directories == 1
    assert (destination / "empty").is_dir()


def test_restore_failure_preserves_destination(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("backup", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "precious.txt").write_text("keep me", encoding="utf-8")

    with patch("backup_tool.snapshot_engine.restore_entry_metadata", side_effect=RestoreError("fail mid-restore")):
        with pytest.raises(RestoreError, match="fail mid-restore"):
            engine.restore_snapshot(manifest, destination, force=True)

    assert (destination / "precious.txt").read_text(encoding="utf-8") == "keep me"


def test_verify_file_content_checks_manifest_size(engine: SnapshotEngine, tmp_path: Path):
    payload = b"payload"
    blob = engine.object_store.put_bytes(payload)
    entry = FileEntry(type="file", hash=blob.hash_hex, size=blob.size + 1)
    assert verify_file_content(engine.object_store, entry) is False


def test_build_snapshot_skips_unsupported_file_type(engine: SnapshotEngine, source_dir: Path):
    if os.name == "nt":
        pytest.skip("FIFO creation is not portable on Windows")
    fifo = source_dir / "pipe"
    os.mkfifo(fifo)
    (source_dir / "ok.txt").write_text("ok", encoding="utf-8")
    result = engine.build_snapshot(source_dir, None)
    assert result.manifest.status == "partial"
    assert any(item.path == "pipe" and "unsupported file type" in item.reason for item in result.skipped)
    assert "ok.txt" in result.manifest.files


def test_strict_abort_does_not_store_skipped_file_blobs(repo: Repository, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    skip_hash = sha256(b"skip").hexdigest()
    result = repo.backup(source_dir, strict=True, skip_predicate=skip_skip_me)
    assert result.manifest is None
    assert not repo.object_store.exists(skip_hash)


def test_gc_reclaims_orphan_blob(repo: Repository):
    orphan_hash = sha256(b"orphan").hexdigest()
    repo.object_store.put_bytes(b"orphan")
    gc = repo.gc()
    assert orphan_hash in gc.deleted_blobs


def test_unstable_skip_does_not_store_blob(engine: SnapshotEngine, source_dir: Path):
    (source_dir / "volatile.txt").write_text("data", encoding="utf-8")

    def skip_volatile(path: Path, _manifest_path: str) -> str | None:
        if path.name == "volatile.txt":
            return "file changed while being read"
        return None

    result = engine.build_snapshot(source_dir, None, skip_predicate=skip_volatile)
    assert result.manifest.status == "partial"
    assert engine.object_store.iter_hashes() == []


def test_file_mode_stored_without_type_bits(engine: SnapshotEngine, source_dir: Path):
    path = source_dir / "mode.txt"
    path.write_text("x", encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    entry = engine.build_snapshot(source_dir, None).manifest.files["mode.txt"]
    assert entry.mode == stat.S_IMODE(path.stat().st_mode)


def test_cli_backup_accepts_absolute_exclude(repo_path: Path, source_dir: Path):
    from backup_tool.cli import main

    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    main(["init", "--repo", str(repo_path)])
    assert main(["backup", str(source_dir), "--repo", str(repo_path), "--exclude", "/etc"]) == 0
