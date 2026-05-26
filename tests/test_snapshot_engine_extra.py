"""Additional snapshot engine coverage tests."""

from pathlib import Path

import pytest

from backup_tool.errors import ManifestError, RestoreError
from backup_tool.manifest import FileEntry, Manifest
from backup_tool.object_store import ObjectStore
from backup_tool.snapshot_engine import SnapshotEngine


@pytest.fixture
def engine(tmp_path: Path) -> SnapshotEngine:
    store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    store.init()
    return SnapshotEngine(store)


def test_restore_snapshot_to_existing_empty_directory(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("a", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    destination.mkdir()
    engine.restore_snapshot(manifest, destination)
    assert (destination / "a.txt").exists()


def test_restore_snapshot_existing_file_requires_force(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("a", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore.txt"
    destination.write_text("blocker", encoding="utf-8")
    with pytest.raises(RestoreError, match="already exists"):
        engine.restore_snapshot(manifest, destination)


def test_restore_snapshot_unsupported_entry_type():
    with pytest.raises(ManifestError, match="Unsupported file entry type"):
        FileEntry.from_dict({"type": "device"})


def test_restore_snapshot_missing_file_hash():
    with pytest.raises(ManifestError, match="missing hash"):
        FileEntry.from_dict({"type": "file"})


def test_build_snapshot_increments_diff_against_previous(engine: SnapshotEngine, source_dir: Path):
    (source_dir / "a.txt").write_text("v1", encoding="utf-8")
    first = engine.build_snapshot(source_dir, None)
    (source_dir / "a.txt").write_text("v2", encoding="utf-8")
    second = engine.build_snapshot(source_dir, first.manifest)
    assert second.diff.changed == ["a.txt"]
