"""Tests for remaining correctness and CLI regression gaps."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import pytest

from backup_tool import chunking as chunking_module
from backup_tool.cli import main
from backup_tool.errors import IntegrityError, ManifestError, RestoreError
from backup_tool.lock import RepositoryLock
from backup_tool.manifest import FileEntry, Manifest, write_manifest_digest
from backup_tool.paths import manifest_path_matches_exclude_pattern
from backup_tool.repository import Repository
from backup_tool.snapshot_engine import SnapshotEngine
from tests.conftest import (
    TEST_SNAPSHOT_ID,
    manifest_hash,
    symlink_required,
)


def test_partial_backup_does_not_promote_orphan_staged_blobs(
    repo: Repository,
    source_dir: Path,
    monkeypatch,
):
    """Unstable store pass may stage blobs; only manifest-referenced hashes are promoted."""

    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "volatile.txt").write_text("volatile", encoding="utf-8")

    orphan_hash: str | None = None
    real_store = chunking_module.store_file

    def store_with_orphan_staging(
        store,
        path: Path,
        *,
        dry_run: bool = False,
        chunk_size: int = chunking_module.DEFAULT_CHUNK_SIZE,
    ):
        nonlocal orphan_hash
        if path.name == "volatile.txt":
            orphan = store.put_bytes(b"orphan-from-failed-store-pass")
            orphan_hash = orphan.hash_hex
            return chunking_module.StoredFileInfo(
                manifest_hash("wrong-hash"),
                path.stat().st_size,
                None,
                1,
                path.stat().st_size,
            )
        return real_store(store, path, dry_run=dry_run, chunk_size=chunk_size)

    monkeypatch.setattr(
        "backup_tool.snapshot_engine.store_file",
        store_with_orphan_staging,
    )
    result = repo.backup(source_dir)

    assert result.committed
    assert result.manifest is not None
    assert result.manifest.status == "partial"
    assert "keep.txt" in result.manifest.files
    assert "volatile.txt" not in result.manifest.files
    assert orphan_hash is not None
    keep_hash = result.manifest.files["keep.txt"].hash
    assert keep_hash is not None
    assert repo.object_store.has_valid_blob(keep_hash)
    assert not repo.object_store.get_path(orphan_hash).exists()


def test_exclude_dir_glob_does_not_match_nested_py_files(
    engine: SnapshotEngine,
    source_dir: Path,
):
    (source_dir / "dir").mkdir()
    (source_dir / "dir" / "top.py").write_text("x", encoding="utf-8")
    (source_dir / "dir" / "sub").mkdir()
    (source_dir / "dir" / "sub" / "nested.py").write_text("x", encoding="utf-8")

    assert manifest_path_matches_exclude_pattern("dir/top.py", "dir/*.py")
    assert not manifest_path_matches_exclude_pattern("dir/sub/nested.py", "dir/*.py")

    result = engine.build_snapshot(source_dir, None, excludes=["dir/*.py"])

    assert "dir/top.py" not in result.manifest.files
    assert "dir/sub/nested.py" in result.manifest.files


def test_cli_invalid_arguments_return_documented_exit_code_one():
    assert main(["backup"]) == 1
    assert main(["backup", ".", "--repo"]) == 1


def test_cli_invalid_subcommand_returns_exit_code_one():
    assert main(["not-a-command"]) == 1


@symlink_required
def test_cli_restore_partial_symlink_returns_exit_code_three(
    repo: Repository,
    source_dir: Path,
    repo_path: Path,
    tmp_path: Path,
    monkeypatch,
):
    (source_dir / "target.txt").write_text("linked", encoding="utf-8")
    (source_dir / "link.txt").symlink_to("target.txt")
    repo.backup(source_dir)

    destination = tmp_path / "new_restore"

    def fail_symlink(*_args, **_kwargs):
        raise OSError("symlink denied")

    monkeypatch.setattr("backup_tool.snapshot_engine.os.symlink", fail_symlink)

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(
            [
                "restore",
                "latest",
                "--repo",
                str(repo_path),
                "--to",
                str(destination),
            ]
        )

    assert code == 3
    assert destination.exists()
    assert (destination / "target.txt").read_text(encoding="utf-8") == "linked"
    assert "partial" in stderr.getvalue().lower() or "warning" in stderr.getvalue().lower()


def test_check_reports_manifest_digest_hash_failure(
    repo: Repository,
    source_dir: Path,
    monkeypatch,
):
    from backup_tool.errors import HashError

    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)

    def fail_hash(_path: Path, chunk_size: int = 1024 * 1024):
        raise HashError("read failed")

    monkeypatch.setattr("backup_tool.manifest.hash_file", fail_hash)
    result = repo.check()

    assert result.ok is False
    assert any("Could not verify manifest digest" in error for error in result.errors)


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


def test_check_reports_missing_derived_stat_keys(
    repo: Repository,
    source_dir: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    manifest = repo.manifest_store.latest()
    path = repo.manifest_store.path_for(manifest.snapshot_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["stats"] = {}
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_manifest_digest(path)

    result = repo.check()

    assert result.ok is False
    assert any("stats.entry_count is missing" in error for error in result.errors)
    assert any("stats.errors is missing" in error for error in result.errors)


def test_has_valid_blob_returns_false_when_verify_raises_store_error(
    engine: SnapshotEngine,
    monkeypatch,
):
    from backup_tool.errors import StoreError

    store = engine.object_store
    blob = store.put_bytes(b"payload")

    def fail_verify(_hash_hex: str) -> bool:
        raise StoreError("Could not read blob")

    monkeypatch.setattr(store, "verify_blob", fail_verify)
    assert store.has_valid_blob(blob.hash_hex) is False


def test_has_valid_blob_returns_false_on_hash_error(
    engine: SnapshotEngine,
    monkeypatch,
):
    from backup_tool.errors import HashError

    store = engine.object_store
    blob = store.put_bytes(b"payload")

    def fail_hash(_path, chunk_size=1024 * 1024):
        raise HashError("read failed")

    monkeypatch.setattr("backup_tool.object_store.hash_file", fail_hash)
    assert store.has_valid_blob(blob.hash_hex) is False


def test_has_valid_blob_returns_false_on_integrity_error(
    engine: SnapshotEngine,
    monkeypatch,
):
    store = engine.object_store
    blob = store.put_bytes(b"payload")

    def fail_verify(_hash_hex: str) -> bool:
        raise IntegrityError("Hash mismatch")

    monkeypatch.setattr(store, "verify_blob", fail_verify)
    assert store.has_valid_blob(blob.hash_hex) is False


def test_check_reports_manifest_topology_conflict_on_disk(
    repo: Repository,
    source_dir: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    path = repo.manifest_store.path_for(repo.manifest_store.latest().snapshot_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"] = {
        "parent": {"type": "file", "hash": manifest_hash("parent"), "size": 1},
        "parent/child.txt": {"type": "file", "hash": manifest_hash("child"), "size": 1},
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_manifest_digest(path)

    result = repo.check()

    assert result.ok is False
    assert any("non-directory ancestor" in error for error in result.errors)


def test_manifest_from_dict_rejects_invalid_created_at():
    payload = {
        "version": 1,
        "snapshot_id": TEST_SNAPSHOT_ID,
        "created_at": "not-a-timestamp",
        "source": "src",
        "hash_algorithm": "sha256",
        "status": "complete",
        "stats": {},
        "files": {},
    }
    with pytest.raises(ManifestError, match="Invalid manifest created_at"):
        Manifest.from_dict(payload)


def test_repository_lock_acquire_leaves_no_temp_files(tmp_path: Path):
    lock_path = tmp_path / "lock"
    with RepositoryLock(lock_path):
        assert lock_path.exists()
        assert not list(lock_path.parent.glob(".lock.*.tmp"))


def test_file_entry_from_dict_rejects_irrelevant_hash_on_symlink():
    with pytest.raises(ManifestError, match="symlink entry must not include hash"):
        FileEntry.from_dict(
            {
                "type": "symlink",
                "target": "dest",
                "hash": manifest_hash("unused"),
            }
        )


def test_check_rejects_manifest_with_non_finite_mtime(
    repo: Repository,
    source_dir: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    path = repo.manifest_store.path_for(repo.manifest_store.latest().snapshot_id)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"]["a.txt"]["mtime"] = float("nan")
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_manifest_digest(path)

    result = repo.check()

    assert result.ok is False
    assert any("mtime must be finite" in error for error in result.errors)


def test_restore_empty_snapshot_with_file_path_raises(
    engine: SnapshotEngine,
    source_dir: Path,
    tmp_path: Path,
):
    manifest = engine.build_snapshot(source_dir, None).manifest
    assert manifest is not None
    assert len(manifest.files) == 0

    with pytest.raises(RestoreError, match="No files matched"):
        engine.restore_snapshot(manifest, tmp_path / "restore", file_path="missing.txt")


def test_cli_main_catches_parser_system_exit():
    with patch("backup_tool.cli.BackupToolArgumentParser.parse_args") as parse_args:
        parse_args.side_effect = SystemExit(1)
        assert main(["backup", str(Path.cwd())]) == 1


def test_cli_main_maps_non_integer_system_exit_to_one():
    with patch("backup_tool.cli.BackupToolArgumentParser.parse_args") as parse_args:
        parse_args.side_effect = SystemExit("usage")
        assert main(["backup", str(Path.cwd())]) == 1
