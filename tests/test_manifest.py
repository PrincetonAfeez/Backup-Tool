"""Tests for backup_tool.manifest."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from backup_tool.errors import ManifestError
from backup_tool.manifest import MANIFEST_VERSION, FileEntry, Manifest, ManifestStore


def _sample_manifest(**overrides) -> Manifest:
    data = {
        "snapshot_id": "2026-01-01T00-00-00Z_abcd1234",
        "created_at": "2026-01-01T00:00:00Z",
        "source": "source",
        "status": "complete",
        "stats": {"file_count": 0},
        "files": {},
    }
    data.update(overrides)
    return Manifest(**data)


def test_file_entry_round_trip_whole_file():
    entry = FileEntry(type="file", hash="abc", size=3, mtime=1.0, mode=0o644)
    loaded = FileEntry.from_dict(entry.to_dict())
    assert loaded == entry
    assert loaded.identity() == ("file", "abc", None)


def test_file_entry_round_trip_chunked():
    entry = FileEntry(type="file", hash="full", size=10, chunks=("c1", "c2"))
    loaded = FileEntry.from_dict(entry.to_dict())
    assert loaded.chunks == ("c1", "c2")
    assert loaded.identity() == ("file", "full", ("c1", "c2"))


def test_file_entry_symlink_round_trip():
    entry = FileEntry(type="symlink", target="dest.txt", mode=0o777)
    loaded = FileEntry.from_dict(entry.to_dict())
    assert loaded.identity() == ("symlink", "dest.txt")


@pytest.mark.parametrize(
    "data,match",
    [
        ({"type": "dir"}, "Unsupported file entry type"),
        ({"type": "file"}, "missing hash"),
        ({"type": "symlink"}, "missing target"),
        ({"type": "file", "hash": "x", "chunks": []}, "non-empty list"),
        ({"type": "file", "hash": "x", "chunks": "bad"}, "non-empty list"),
    ],
)
def test_file_entry_from_dict_validation(data, match):
    with pytest.raises(ManifestError, match=match):
        FileEntry.from_dict(data)


def test_manifest_to_dict_sorts_files():
    manifest = _sample_manifest(
        files={
            "b.txt": FileEntry(type="file", hash="2"),
            "a.txt": FileEntry(type="file", hash="1"),
        }
    )
    data = manifest.to_dict()
    assert list(data["files"]) == ["a.txt", "b.txt"]


def test_manifest_from_dict_validation_errors():
    with pytest.raises(ManifestError, match="Unsupported manifest version"):
        Manifest.from_dict({"version": 99})
    with pytest.raises(ManifestError, match="files must be an object"):
        Manifest.from_dict({"version": 1, "files": []})
    with pytest.raises(ManifestError, match="missing required keys"):
        Manifest.from_dict({"version": 1, "files": {}})


def test_manifest_duplicate_normalized_paths():
    payload = {
        "version": 1,
        "snapshot_id": "id",
        "created_at": "t",
        "source": "src",
        "hash_algorithm": "sha256",
        "status": "complete",
        "stats": {},
        "files": {
            "foo/bar": {"type": "file", "hash": "1"},
            "foo\\bar": {"type": "file", "hash": "2"},
        },
    }
    with pytest.raises(ManifestError, match="Duplicate normalized"):
        Manifest.from_dict(payload)


def test_manifest_store_round_trip(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    manifest = _sample_manifest()
    path = store.save(manifest)
    assert path.exists()
    loaded = store.load(manifest.snapshot_id)
    assert loaded.snapshot_id == manifest.snapshot_id


def test_manifest_store_latest_and_list(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    first = _sample_manifest(snapshot_id="2026-01-01T00-00-00Z_first", created_at="2026-01-01T00:00:00Z")
    second = _sample_manifest(snapshot_id="2026-01-02T00-00-00Z_second", created_at="2026-01-02T00:00:00Z")
    store.save(first)
    store.save(second)
    assert store.latest() == second
    assert len(store.list_manifests()) == 2
    assert len(store.list_paths()) == 2


def test_manifest_store_invalid_snapshot_id(tmp_path: Path):
    store = ManifestStore(tmp_path)
    with pytest.raises(ManifestError, match="Invalid snapshot id"):
        store.path_for("../bad")


def test_manifest_store_save_twice_raises(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    manifest = _sample_manifest()
    store.save(manifest)
    with pytest.raises(ManifestError, match="already exists"):
        store.save(manifest)


def test_manifest_store_load_missing(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    with pytest.raises(ManifestError, match="not found"):
        store.load("missing")


def test_manifest_store_load_path_invalid_json(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    with pytest.raises(ManifestError, match="Could not load manifest"):
        store.load_path(bad)


def test_manifest_store_list_paths_missing_dir(tmp_path: Path):
    store = ManifestStore(tmp_path / "missing")
    assert store.list_paths() == []
    assert store.latest() is None
