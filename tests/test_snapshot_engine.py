"""Tests for backup_tool.snapshot_engine."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backup_tool.errors import ManifestError, RestoreError
from backup_tool.object_store import ObjectStore
from backup_tool.snapshot_engine import SkippedItem, SnapshotEngine, SnapshotResult
from tests.conftest import symlink_required


@pytest.fixture
def engine(tmp_path: Path) -> SnapshotEngine:
    store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    store.init()
    return SnapshotEngine(store)


def test_skipped_item_to_dict():
    item = SkippedItem("path", "reason")
    assert item.to_dict() == {"path": "path", "reason": "reason"}


def test_snapshot_result_status_aborted():
    result = SnapshotResult(manifest=None, diff=None, committed=False, dry_run=False)
    assert result.status == "aborted"


def test_build_snapshot_requires_directory(engine: SnapshotEngine, tmp_path: Path):
    missing = tmp_path / "missing"
    with pytest.raises(ManifestError, match="existing directory"):
        engine.build_snapshot(missing, None)


def test_build_snapshot_empty_directory(engine: SnapshotEngine, source_dir: Path):
    result = engine.build_snapshot(source_dir, None)
    assert result.manifest is not None
    assert result.manifest.stats["file_count"] == 0
    assert result.status == "complete"


def test_build_snapshot_respects_excludes(engine: SnapshotEngine, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip.tmp").write_text("skip", encoding="utf-8")
    result = engine.build_snapshot(source_dir, None, excludes=["*.tmp"])
    assert "keep.txt" in result.manifest.files
    assert "skip.tmp" not in result.manifest.files


def test_build_snapshot_dry_run_does_not_store(engine: SnapshotEngine, source_dir: Path):
    (source_dir / "a.txt").write_text("a", encoding="utf-8")
    result = engine.build_snapshot(source_dir, None, dry_run=True)
    assert result.dry_run is True
    assert result.manifest.status == "dry-run"
    assert engine.object_store.iter_hashes() == []


def test_build_snapshot_strict_aborts(skip_predicate, engine: SnapshotEngine, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    result = engine.build_snapshot(source_dir, None, strict=True, skip_predicate=skip_predicate)
    assert result.manifest is None
    assert result.committed is False


def test_build_snapshot_partial_when_not_strict(skip_predicate, engine: SnapshotEngine, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    result = engine.build_snapshot(source_dir, None, skip_predicate=skip_predicate)
    assert result.manifest.status == "partial"


def test_restore_snapshot_refuses_nonempty_destination(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("a", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "existing.txt").write_text("x", encoding="utf-8")
    with pytest.raises(RestoreError, match="not empty"):
        engine.restore_snapshot(manifest, destination)


def test_restore_snapshot_force_overwrites(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "old.txt").write_text("old", encoding="utf-8")
    result = engine.restore_snapshot(manifest, destination, force=True)
    assert result.restored_files == 1
    assert (destination / "a.txt").read_text(encoding="utf-8") == "hello"


def test_restore_snapshot_single_file(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("aaa", encoding="utf-8")
    (source_dir / "b.txt").write_text("bbb", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    result = engine.restore_snapshot(manifest, destination, file_path="a.txt")
    assert result.restored_files == 1
    assert (destination / "a.txt").exists()
    assert not (destination / "b.txt").exists()


def test_restore_snapshot_no_matches_raises(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("a", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    with pytest.raises(RestoreError, match="No files matched"):
        engine.restore_snapshot(manifest, tmp_path / "restore", file_path="missing.txt")


@symlink_required
def test_build_and_restore_symlink(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "target.txt").write_text("linked", encoding="utf-8")
    (source_dir / "link.txt").symlink_to("target.txt")
    manifest = engine.build_snapshot(source_dir, None).manifest
    assert manifest.files["link.txt"].type == "symlink"
    destination = tmp_path / "restore"
    result = engine.restore_snapshot(manifest, destination)
    assert result.restored_symlinks == 1
    assert os.readlink(destination / "link.txt") == "target.txt"


def test_is_excluded_directory_prefix(engine: SnapshotEngine):
    assert engine._is_excluded("repo/data.txt", ["repo"]) is True
    assert engine._is_excluded("other/data.txt", ["repo"]) is False


def test_is_excluded_path_pattern_does_not_match_basename_only(engine: SnapshotEngine, source_dir: Path):
    nested = source_dir / "tests"
    nested.mkdir()
    (nested / "foo.py").write_text("x", encoding="utf-8")
    (source_dir / "foo.py").write_text("y", encoding="utf-8")
    result = engine.build_snapshot(source_dir, None, excludes=["tests/foo.py"])
    assert "tests/foo.py" not in result.manifest.files
    assert "foo.py" in result.manifest.files
