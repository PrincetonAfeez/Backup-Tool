"""Tests for backup_tool.manifest."""

from pathlib import Path

import pytest

from backup_tool.errors import ManifestError
from backup_tool.manifest import (
    FileEntry,
    Manifest,
    ManifestStore,
    manifest_digest_path,
    manifest_stats_consistency_errors,
    verify_manifest_digest,
    write_manifest_digest,
)
from tests.conftest import (
    MISSING_SNAPSHOT_ID,
    TEST_CREATED_AT,
    TEST_SNAPSHOT_ID,
    TEST_SNAPSHOT_ID_A,
    TEST_SNAPSHOT_ID_B,
    manifest_hash,
)


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


def test_file_entry_round_trip_whole_file():
    file_hash = manifest_hash("abc")
    entry = FileEntry(type="file", hash=file_hash, size=3, mtime=1.0, mode=0o644)
    loaded = FileEntry.from_dict(entry.to_dict())
    assert loaded == entry
    assert loaded.identity() == ("file", file_hash, None, 3)


def test_file_entry_round_trip_chunked():
    file_hash = manifest_hash("full")
    chunk_a = manifest_hash("c1")
    chunk_b = manifest_hash("c2")
    entry = FileEntry(type="file", hash=file_hash, size=10, chunks=(chunk_a, chunk_b))
    loaded = FileEntry.from_dict(entry.to_dict())
    assert loaded.chunks == (chunk_a, chunk_b)
    assert loaded.identity() == ("file", file_hash, (chunk_a, chunk_b), 10)


def test_file_entry_symlink_round_trip():
    entry = FileEntry(type="symlink", target="dest.txt", mode=0o777, is_dir_symlink=False)
    loaded = FileEntry.from_dict(entry.to_dict())
    assert loaded.identity() == ("symlink", "dest.txt")


def test_file_entry_directory_round_trip():
    entry = FileEntry(type="directory", mode=0o755)
    loaded = FileEntry.from_dict(entry.to_dict())
    assert loaded.identity() == ("directory",)


def test_file_entry_direct_construct_rejects_invalid_type():
    with pytest.raises(ManifestError, match="Unsupported file entry type"):
        FileEntry(type="device")


def test_file_entry_direct_construct_rejects_missing_file_hash():
    with pytest.raises(ManifestError, match="missing hash"):
        FileEntry(type="file")


def test_file_entry_direct_construct_rejects_invalid_symlink_mtime():
    with pytest.raises(ManifestError, match="mtime must be numeric"):
        FileEntry(type="symlink", target="dest", mtime="bad")  # type: ignore[arg-type]


@pytest.mark.parametrize("mtime", [float("nan"), float("inf"), float("-inf")])
def test_file_entry_rejects_non_finite_mtime(mtime: float):
    with pytest.raises(ManifestError, match="mtime must be finite"):
        FileEntry(
            type="file",
            hash=manifest_hash("x"),
            size=1,
            mtime=mtime,
        )
    with pytest.raises(ManifestError, match="mtime must be finite"):
        FileEntry.from_dict(
            {"type": "file", "hash": manifest_hash("x"), "mtime": mtime},
        )


@pytest.mark.parametrize(
    "data,match",
    [
        ({"type": "dir"}, "Unsupported file entry type"),
        ({"type": "file"}, "missing hash"),
        ({"type": "symlink"}, "missing target"),
        ({"type": "file", "hash": manifest_hash("x"), "chunks": []}, "non-empty list"),
        ({"type": "file", "hash": manifest_hash("x"), "chunks": "bad"}, "non-empty list"),
        ({"type": "file", "hash": "short"}, "Invalid SHA-256 hash length"),
        ({"type": "file", "hash": 1}, "hash must be a string"),
        (
            {"type": "file", "hash": manifest_hash("x"), "chunks": [1]},
            "chunks\\[0\\] must be a string",
        ),
    ],
)
def test_file_entry_from_dict_validation(data, match):
    with pytest.raises(ManifestError, match=match):
        FileEntry.from_dict(data)


def test_manifest_to_dict_sorts_files():
    manifest = _sample_manifest(
        files={
            "b.txt": FileEntry(type="file", hash=manifest_hash("2")),
            "a.txt": FileEntry(type="file", hash=manifest_hash("1")),
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


def test_manifest_rejects_unsupported_hash_algorithm():
    payload = {
        "version": 1,
        "snapshot_id": TEST_SNAPSHOT_ID,
        "created_at": TEST_CREATED_AT,
        "source": "src",
        "hash_algorithm": "sha512",
        "status": "complete",
        "stats": {},
        "files": {},
    }
    with pytest.raises(ManifestError, match="Unsupported manifest hash algorithm"):
        Manifest.from_dict(payload)


def test_manifest_rejects_invalid_status():
    payload = {
        "version": 1,
        "snapshot_id": TEST_SNAPSHOT_ID,
        "created_at": TEST_CREATED_AT,
        "source": "src",
        "hash_algorithm": "sha256",
        "status": "broken",
        "stats": {},
        "files": {},
    }
    with pytest.raises(ManifestError, match="Unsupported manifest status"):
        Manifest.from_dict(payload)


def test_manifest_rejects_non_object_stats():
    payload = {
        "version": 1,
        "snapshot_id": TEST_SNAPSHOT_ID,
        "created_at": TEST_CREATED_AT,
        "source": "src",
        "hash_algorithm": "sha256",
        "status": "complete",
        "stats": [],
        "files": {},
    }
    with pytest.raises(ManifestError, match="Manifest stats must be an object"):
        Manifest.from_dict(payload)


def test_manifest_direct_construct_normalizes_file_paths():
    entry = FileEntry(type="file", hash=manifest_hash("x"), size=1)
    manifest = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={"entry_count": 1, "regular_file_count": 1},
        files={"foo\\bar": entry},
    )
    assert "foo/bar" in manifest.files
    assert "foo\\bar" not in manifest.files


def test_manifest_direct_construct_rejects_duplicate_normalized_paths():
    entry_a = FileEntry(type="file", hash=manifest_hash("a"), size=1)
    entry_b = FileEntry(type="file", hash=manifest_hash("b"), size=1)
    with pytest.raises(ManifestError, match="Duplicate normalized"):
        Manifest(
            snapshot_id=TEST_SNAPSHOT_ID,
            created_at=TEST_CREATED_AT,
            source="src",
            status="complete",
            stats={"entry_count": 2, "regular_file_count": 2},
            files={"foo/bar": entry_a, "foo\\bar": entry_b},
        )


def test_manifest_direct_construct_rejects_non_file_entry():
    with pytest.raises(ManifestError, match="Manifest entry must be a FileEntry"):
        Manifest(
            snapshot_id=TEST_SNAPSHOT_ID,
            created_at=TEST_CREATED_AT,
            source="src",
            status="complete",
            stats={"entry_count": 0},
            files={"bad.txt": {"type": "file", "hash": manifest_hash("x")}},  # type: ignore[dict-item]
        )


def test_manifest_rejects_file_with_nested_path_direct_construct():
    parent = FileEntry(type="file", hash=manifest_hash("a"), size=1)
    child = FileEntry(type="file", hash=manifest_hash("b"), size=1)
    with pytest.raises(ManifestError, match="non-directory ancestor"):
        Manifest(
            snapshot_id=TEST_SNAPSHOT_ID,
            created_at=TEST_CREATED_AT,
            source="src",
            status="complete",
            stats={"entry_count": 2, "regular_file_count": 2},
            files={"a": parent, "a/b.txt": child},
        )


def test_manifest_rejects_symlink_with_nested_path_from_dict():
    payload = {
        "version": 1,
        "snapshot_id": TEST_SNAPSHOT_ID,
        "created_at": TEST_CREATED_AT,
        "source": "src",
        "hash_algorithm": "sha256",
        "status": "complete",
        "stats": {},
        "files": {
            "link": {"type": "symlink", "target": "dest"},
            "link/file": {"type": "file", "hash": manifest_hash("x")},
        },
    }
    with pytest.raises(ManifestError, match="non-directory ancestor"):
        Manifest.from_dict(payload)


def test_manifest_allows_directory_with_nested_paths():
    directory = FileEntry(type="directory")
    child = FileEntry(type="file", hash=manifest_hash("b"), size=1)
    manifest = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={"entry_count": 2, "directory_count": 1, "regular_file_count": 1},
        files={"a": directory, "a/b.txt": child},
    )
    assert manifest.files["a"].type == "directory"
    assert manifest.files["a/b.txt"].type == "file"


def test_manifest_duplicate_normalized_paths():
    payload = {
        "version": 1,
        "snapshot_id": TEST_SNAPSHOT_ID,
        "created_at": TEST_CREATED_AT,
        "source": "src",
        "hash_algorithm": "sha256",
        "status": "complete",
        "stats": {},
        "files": {
            "foo/bar": {"type": "file", "hash": manifest_hash("1")},
            "foo\\bar": {"type": "file", "hash": manifest_hash("2")},
        },
    }
    with pytest.raises(ManifestError, match="Duplicate normalized"):
        Manifest.from_dict(payload)


def test_manifest_store_save_writes_digest_sidecar(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    manifest = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={"entry_count": 0},
        files={},
    )
    path = store.save(manifest)
    sidecar = path.with_name(f"{path.name}.sha256")
    assert sidecar.exists()
    loaded = store.load(manifest.snapshot_id)
    assert loaded.snapshot_id == manifest.snapshot_id


def test_manifest_digest_accepts_uppercase_sidecar(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    manifest = _sample_manifest()
    path = store.save(manifest)
    sidecar = manifest_digest_path(path)
    sidecar.write_text(f"{sidecar.read_text(encoding='utf-8').strip().upper()}\n", encoding="utf-8")
    verify_manifest_digest(path)
    loaded = store.load_path(path)
    assert loaded.snapshot_id == manifest.snapshot_id


def test_manifest_digest_rejects_tampered_file(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    manifest = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={"entry_count": 0},
        files={},
    )
    path = store.save(manifest)
    path.write_text('{"version": 1, "tampered": true}\n', encoding="utf-8")
    with pytest.raises(ManifestError, match="digest mismatch"):
        store.load_path(path)


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
    first = _sample_manifest(snapshot_id=TEST_SNAPSHOT_ID_A, created_at=TEST_CREATED_AT)
    second = _sample_manifest(snapshot_id=TEST_SNAPSHOT_ID_B, created_at="2026-01-02T00:00:00.000000Z")
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
        store.load(MISSING_SNAPSHOT_ID)


def test_manifest_store_load_path_invalid_json(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    bad = tmp_path / "bad.json"
    bad.write_text("{bad", encoding="utf-8")
    write_manifest_digest(bad)
    with pytest.raises(ManifestError, match="Could not load manifest"):
        store.load_path(bad)


def test_manifest_store_list_paths_missing_dir(tmp_path: Path):
    store = ManifestStore(tmp_path / "missing")
    assert store.list_paths() == []
    assert store.latest() is None


def test_manifest_rejects_invalid_snapshot_id_on_construct():
    with pytest.raises(ManifestError, match="Invalid snapshot id"):
        Manifest(
            snapshot_id="bad-id",
            created_at=TEST_CREATED_AT,
            source="src",
            status="complete",
            stats={"entry_count": 0},
            files={},
        )


def test_manifest_rejects_invalid_created_at():
    with pytest.raises(ManifestError, match="Invalid manifest created_at"):
        Manifest(
            snapshot_id=TEST_SNAPSHOT_ID,
            created_at="not-a-timestamp",
            source="src",
            status="complete",
            stats={"entry_count": 0},
            files={},
        )


def test_manifest_rejects_impossible_created_at_date():
    with pytest.raises(ManifestError, match="Invalid manifest created_at"):
        Manifest.from_dict(
            {
                "version": 1,
                "snapshot_id": TEST_SNAPSHOT_ID,
                "created_at": "2026-02-30T00:00:00.000000Z",
                "source": "src",
                "hash_algorithm": "sha256",
                "status": "complete",
                "stats": {"entry_count": 0},
                "files": {},
            }
        )


def test_manifest_stats_consistency_errors_detect_mismatch():
    file_hash = manifest_hash("abc")
    entry = FileEntry(type="file", hash=file_hash, size=3)
    manifest = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={
            "entry_count": 99,
            "regular_file_count": 1,
            "directory_count": 0,
            "symlink_count": 0,
            "skipped_files": 0,
        },
        files={"a.txt": entry},
    )
    errors = manifest_stats_consistency_errors(manifest)
    assert any("stats.entry_count" in error for error in errors)


def test_file_entry_direct_construct_normalizes_hash_case():
    lowercase = manifest_hash("payload")
    uppercase = lowercase.upper()
    entry = FileEntry(type="file", hash=uppercase, size=3)
    assert entry.hash == lowercase


def test_file_entry_direct_construct_normalizes_chunk_hashes():
    chunk = manifest_hash("chunk")
    entry = FileEntry(
        type="file",
        hash=manifest_hash("file"),
        size=10,
        chunks=(chunk.upper(),),
    )
    assert entry.chunks == (chunk,)


def test_manifest_direct_construct_rejects_blank_source():
    with pytest.raises(ManifestError, match="source must be a non-empty string"):
        Manifest(
            snapshot_id=TEST_SNAPSHOT_ID,
            created_at=TEST_CREATED_AT,
            source="   ",
            status="complete",
            stats={"entry_count": 0},
            files={},
        )


def test_manifest_stats_consistency_errors_detect_missing_keys():
    manifest = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={},
        files={},
    )
    errors = manifest_stats_consistency_errors(manifest)
    assert any("stats.entry_count is missing" in error for error in errors)


def test_manifest_stats_consistency_errors_detect_errors_mismatch():
    manifest = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={"errors": 0, "skipped_files": 1},
        files={},
        skipped=[{"path": "a.txt", "reason": "skipped"}],
    )
    errors = manifest_stats_consistency_errors(manifest)
    assert any("stats.errors" in error for error in errors)


def test_manifest_rejects_non_string_source():
    with pytest.raises(ManifestError, match="source must be a non-empty string"):
        Manifest.from_dict(
            {
                "version": 1,
                "snapshot_id": TEST_SNAPSHOT_ID,
                "created_at": TEST_CREATED_AT,
                "source": 123,
                "hash_algorithm": "sha256",
                "status": "complete",
                "stats": {"entry_count": 0},
                "files": {},
            }
        )


def test_manifest_rejects_invalid_stats_value():
    with pytest.raises(ManifestError, match="must be an integer"):
        Manifest.from_dict(
            {
                "version": 1,
                "snapshot_id": TEST_SNAPSHOT_ID,
                "created_at": TEST_CREATED_AT,
                "source": "src",
                "hash_algorithm": "sha256",
                "status": "complete",
                "stats": {"entry_count": "many"},
                "files": {},
            }
        )


def test_manifest_rejects_invalid_skipped_items():
    with pytest.raises(ManifestError, match="skipped must be a list"):
        Manifest.from_dict(
            {
                "version": 1,
                "snapshot_id": TEST_SNAPSHOT_ID,
                "created_at": TEST_CREATED_AT,
                "source": "src",
                "hash_algorithm": "sha256",
                "status": "complete",
                "stats": {"entry_count": 0},
                "files": {},
                "skipped": {"path": "a.txt", "reason": "x"},
            }
        )


def test_manifest_load_path_rejects_snapshot_id_mismatch(tmp_path: Path):
    store = ManifestStore(tmp_path)
    store.init()
    manifest = _sample_manifest()
    path = store.save(manifest)
    data = manifest.to_dict()
    data["snapshot_id"] = TEST_SNAPSHOT_ID_B
    path.write_text(__import__("json").dumps(data), encoding="utf-8")
    write_manifest_digest(path)
    with pytest.raises(ManifestError, match="snapshot_id mismatch"):
        store.load_path(path)


def test_file_entry_rejects_irrelevant_fields():
    file_hash = manifest_hash("x")
    with pytest.raises(ManifestError, match="directory entry must not include hash"):
        FileEntry(type="directory", hash=file_hash)
    with pytest.raises(ManifestError, match="symlink entry must not include hash"):
        FileEntry(type="symlink", target="dest", hash=file_hash)
    with pytest.raises(ManifestError, match="file entry must not include target"):
        FileEntry(type="file", hash=file_hash, target="dest")


def test_file_entry_rejects_out_of_range_mode():
    file_hash = manifest_hash("x")
    with pytest.raises(ManifestError, match="mode must be between"):
        FileEntry(type="file", hash=file_hash, mode=-1)
    with pytest.raises(ManifestError, match="mode must be between"):
        FileEntry(type="file", hash=file_hash, mode=0o10000)


def test_manifest_stats_accept_legacy_file_count():
    manifest = Manifest.from_dict(
        {
            "version": 1,
            "snapshot_id": TEST_SNAPSHOT_ID,
            "created_at": TEST_CREATED_AT,
            "source": "src",
            "hash_algorithm": "sha256",
            "status": "complete",
            "stats": {"file_count": 2},
            "files": {},
        }
    )
    assert manifest.stats["entry_count"] == 2
