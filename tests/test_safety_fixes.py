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
from tests.conftest import TEST_SNAPSHOT_ID, manifest_hash, skip_skip_me


@pytest.fixture
def engine(tmp_path: Path) -> SnapshotEngine:
    store = ObjectStore(tmp_path / "objects", tmp_path / "tmp")
    store.init()
    return SnapshotEngine(store)


def test_validate_exclude_pattern_allows_absolute_style():
    assert validate_exclude_pattern("/etc") == "etc"
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


def test_classify_entries_treats_mode_only_change_as_unchanged():
    shared_hash = manifest_hash("h")
    previous = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mode=0o644)}
    current = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mode=0o600)}
    result = classify_entries(current, previous)
    assert result.unchanged == ["a.txt"]
    assert result.changed == []


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


def test_store_pass_hash_mismatch_skips_file(engine: SnapshotEngine, source_dir: Path):
    from backup_tool.chunking import StoredFileInfo

    (source_dir / "volatile.txt").write_text("stable", encoding="utf-8")
    bad_hash = manifest_hash("wrong")

    def fake_store(_store, _path, *, dry_run=False, chunk_size=1048576):
        return StoredFileInfo(bad_hash, 6, None, 1, 6)

    with patch("backup_tool.snapshot_engine.store_file", side_effect=fake_store):
        result = engine.build_snapshot(source_dir, None)

    assert "volatile.txt" not in result.manifest.files
    assert any(item.path == "volatile.txt" for item in result.skipped)


def test_dry_run_counts_corrupt_existing_blob_as_new(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    first = repo.backup(source_dir)
    blob_hash = first.manifest.files["a.txt"].hash
    assert blob_hash is not None
    repo.object_store.get_path(blob_hash).write_text("corrupt", encoding="utf-8")

    dry = repo.backup(source_dir, dry_run=True)
    assert dry.manifest.stats["new_bytes_stored"] > 0


def test_gc_dry_run_aggressive_does_not_create_quarantine(repo: Repository):
    hash_hex, wrong_path = _place_misplaced_blob_helper(repo, b"misplaced")
    quarantine = repo.tmp_dir / "quarantine"
    result = repo.gc(dry_run=True, aggressive=True)
    assert wrong_path.exists()
    assert not quarantine.exists()
    assert any(hash_hex in item for item in result.quarantined_malformed)


def _place_misplaced_blob_helper(repo: Repository, payload: bytes) -> tuple[str, Path]:
    hash_hex = sha256(payload).hexdigest()
    wrong_path = repo.object_store.objects_dir / "zz" / hash_hex
    wrong_path.parent.mkdir(parents=True, exist_ok=True)
    wrong_path.write_bytes(payload)
    return hash_hex, wrong_path


def test_restore_force_preserves_unrelated_old_sibling(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "precious.txt").write_text("keep", encoding="utf-8")
    unrelated = destination.parent / ".restore-old-unrelated"
    unrelated.write_text("unrelated data", encoding="utf-8")

    engine.restore_snapshot(manifest, destination, force=True)

    assert unrelated.read_text(encoding="utf-8") == "unrelated data"
    assert (destination / "a.txt").read_text(encoding="utf-8") == "hello"


def test_manifest_save_rolls_back_when_sidecar_write_fails(tmp_path: Path, monkeypatch):
    import os

    from backup_tool.manifest import Manifest, ManifestStore
    from tests.conftest import TEST_CREATED_AT, TEST_SNAPSHOT_ID

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
    real_replace = os.replace

    def fail_replace_json(src, dst):
        if str(dst).endswith(".json") and not str(dst).endswith(".sha256"):
            raise OSError("json write failed")
        return real_replace(src, dst)

    monkeypatch.setattr("backup_tool.manifest.os.replace", fail_replace_json)
    with pytest.raises(OSError, match="json write failed"):
        store.save(manifest)

    path = store.path_for(manifest.snapshot_id)
    assert not path.exists()
    assert not path.with_name(f"{path.name}.sha256").exists()


def test_migrate_manifest_digests_writes_missing_sidecars(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    path = repo.manifest_store.path_for(repo.manifest_store.latest().snapshot_id)
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.unlink()

    migrate_result = repo.migrate_manifest_digests()
    assert path.stem in migrate_result.migrated
    assert sidecar.exists()
    assert repo.manifest_store.load(path.stem).snapshot_id == path.stem


def test_migrate_manifest_digests_skips_invalid_manifest(repo: Repository, tmp_path: Path):
    invalid = repo.snapshots_dir / f"{TEST_SNAPSHOT_ID}.json"
    invalid.write_text('{"version": 1, "bad": true}', encoding="utf-8")
    migrate_result = repo.migrate_manifest_digests()
    assert TEST_SNAPSHOT_ID not in migrate_result.migrated
    assert any(TEST_SNAPSHOT_ID in item for item in migrate_result.skipped)
    assert not invalid.with_name(f"{invalid.name}.sha256").exists()


def test_repo_json_non_object_raises_repository_error(repo_path: Path):
    repo_path.mkdir(parents=True, exist_ok=True)
    (repo_path / "objects").mkdir()
    (repo_path / "snapshots").mkdir()
    (repo_path / "repo.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(RepositoryError, match="must be an object"):
        Repository(repo_path).list_snapshots()


def test_manifest_non_object_root_rejected(tmp_path: Path):
    from backup_tool.manifest import ManifestStore, write_manifest_digest

    store = ManifestStore(tmp_path)
    store.init()
    path = tmp_path / "bad.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    write_manifest_digest(path)
    with pytest.raises(ManifestError, match="root must be an object"):
        store.load_path(path)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("size", -1, "size must be >= 0"),
        ("size", "big", "size must be an integer"),
        ("mtime", "not-a-number", "mtime must be numeric"),
        ("mode", "644", "mode must be an integer"),
    ],
)
def test_file_entry_rejects_invalid_metadata(field, value, match):
    payload = {"type": "file", "hash": manifest_hash("x"), field: value}
    with pytest.raises(ManifestError, match=match):
        FileEntry.from_dict(payload)


def test_symlink_entry_rejects_non_string_target():
    with pytest.raises(ManifestError, match="target must be a string"):
        FileEntry.from_dict({"type": "symlink", "target": 123})


def test_restore_entry_metadata_type_error_raises_restore_error(tmp_path: Path):
    from backup_tool.metadata import restore_entry_metadata

    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")
    entry = FileEntry(type="file", hash=manifest_hash("x"), size=1, mode=0o644)
    warnings: list[str] = []

    with patch("backup_tool.metadata.os.chmod", side_effect=TypeError("bad mode")):
        with pytest.raises(RestoreError, match="Invalid mode metadata"):
            restore_entry_metadata(path, entry, warnings)


def test_walk_source_skips_directory_lstat_failure(engine: SnapshotEngine, source_dir: Path):
    nested = source_dir / "nested" / "file.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("inside", encoding="utf-8")
    real_lstat = Path.lstat

    def selective_lstat(self: Path):
        if self == source_dir / "nested":
            raise OSError(13, "permission denied")
        return real_lstat(self)

    with patch.object(Path, "lstat", selective_lstat):
        result = engine.build_snapshot(source_dir, None)

    assert "nested/file.txt" not in result.manifest.files
    assert any(
        item.path == "nested" and "could not stat directory" in item.reason
        for item in result.skipped
    )


def test_restore_force_rollback_after_final_replace_failure(
    engine: SnapshotEngine,
    source_dir: Path,
    tmp_path: Path,
    monkeypatch,
):
    import os

    (source_dir / "a.txt").write_text("backup", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "precious.txt").write_text("keep me", encoding="utf-8")
    dest_resolved = destination.resolve()
    real_replace = os.replace

    def fail_promote_staging(src, dst):
        if (
            Path(dst).resolve() == dest_resolved
            and ".restore-old-" not in Path(src).name
        ):
            raise OSError("promote failed")
        return real_replace(src, dst)

    monkeypatch.setattr("backup_tool.snapshot_engine.os.replace", fail_promote_staging)

    with pytest.raises(OSError, match="promote failed"):
        engine.restore_snapshot(manifest, destination, force=True)

    assert (destination / "precious.txt").read_text(encoding="utf-8") == "keep me"


def test_walk_source_records_scan_failure(engine: SnapshotEngine, source_dir: Path):
    nested = source_dir / "nested"
    nested.mkdir()
    (nested / "file.txt").write_text("inside", encoding="utf-8")
    real_walk = os.walk

    def walk_with_scan_failure(top, *args, **kwargs):
        onerror = kwargs.pop("onerror", None)
        for root, dirnames, filenames in real_walk(top, *args, onerror=onerror, **kwargs):
            if Path(root) == source_dir and onerror is not None:
                exc = OSError(13, "Permission denied", str(nested))
                onerror(exc)
                dirnames.clear()
            yield root, dirnames, filenames

    with patch("backup_tool.snapshot_engine.os.walk", walk_with_scan_failure):
        result = engine.build_snapshot(source_dir, None)

    assert "nested/file.txt" not in result.manifest.files
    assert any(
        item.path == "nested" and "could not scan directory" in item.reason
        for item in result.skipped
    )
