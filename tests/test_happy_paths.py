"""Tests for underspecified happy paths (U-Test-11–U-Test-25)."""

from __future__ import annotations

import io
import os
import stat
import time
from contextlib import redirect_stderr
from hashlib import sha256
from pathlib import Path

import pytest

from backup_tool.cli import main
from backup_tool.errors import ManifestError, RepositoryError, RestoreError
from backup_tool.manifest import FileEntry, Manifest
from backup_tool.object_store import DEFAULT_TMP_MAX_AGE_SECONDS
from backup_tool.paths import safe_restore_path
from backup_tool.repository import Repository
from tests.conftest import manifest_hash, skip_skip_me, symlink_required


def test_strict_abort_gc_reclaims_all_scanned_blobs(repo: Repository, source_dir: Path):
    """U-Test-11: blobs stored before strict abort are reclaimed by GC."""
    (source_dir / "keep1.txt").write_text("one", encoding="utf-8")
    (source_dir / "keep2.txt").write_text("two", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")

    result = repo.backup(source_dir, strict=True, skip_predicate=skip_skip_me)
    assert result.manifest is None
    scanned_hashes = list(repo.object_store.iter_hashes())
    assert len(scanned_hashes) == 2

    gc = repo.gc()
    assert set(gc.deleted_blobs) == set(scanned_hashes)
    assert list(repo.object_store.iter_hashes()) == []


@pytest.mark.skipif(os.name == "nt", reason="Unix file modes are not portable on Windows")
def test_mode_only_change_between_backups(repo: Repository, source_dir: Path):
    """U-Test-12: permission-only changes appear in diff.changed."""
    path = source_dir / "same.txt"
    path.write_text("same", encoding="utf-8")
    repo.backup(source_dir)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    second = repo.backup(source_dir)
    assert second.diff is not None
    assert "same.txt" in second.diff.changed


def test_empty_directory_backed_up_and_restored(engine, source_dir: Path, tmp_path: Path):
    """U-Test-13: empty directories are recorded and restored."""
    empty = source_dir / "empty"
    empty.mkdir()
    manifest = engine.build_snapshot(source_dir, None).manifest
    assert manifest.files["empty"].type == "directory"
    destination = tmp_path / "restore"
    result = engine.restore_snapshot(manifest, destination)
    assert result.restored_directories == 1
    assert (destination / "empty").is_dir()


def test_manifest_rejects_borked_status():
    """U-Test-15: unknown status values fail at load time."""
    payload = {
        "version": 1,
        "snapshot_id": "id",
        "created_at": "t",
        "source": "src",
        "hash_algorithm": "sha256",
        "status": "borked",
        "stats": {},
        "files": {},
    }
    with pytest.raises(ManifestError, match="Unsupported manifest status"):
        Manifest.from_dict(payload)


def test_file_entry_from_dict_rejects_short_chunk_hash():
    """U-Test-17: invalid chunk hashes are rejected at load time."""
    with pytest.raises(ManifestError, match="Invalid SHA-256 hash length"):
        FileEntry.from_dict(
            {
                "type": "file",
                "hash": manifest_hash("file"),
                "chunks": ["short"],
            }
        )


def test_cli_init_on_existing_file_returns_one(tmp_path: Path):
    """U-Test-19: init against a path whose parent is a file exits 1."""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["init", "--repo", str(blocker / "repo")])
    assert code == 1


def test_init_nonempty_directory_without_allow_nonempty(tmp_path: Path):
    """U-Test-20: refuse init in a non-empty directory."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    (repo_path / "leftover.txt").write_text("data", encoding="utf-8")
    with pytest.raises(RepositoryError, match="not empty"):
        Repository.init(repo_path)


@symlink_required
def test_safe_restore_path_rejects_symlink_escape_via_destination(tmp_path: Path):
    """U-Test-21: pre-existing symlinks under destination can escape containment."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    dest = tmp_path / "restore"
    dest.mkdir()
    link = dest / "link-out"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RestoreError, match="escapes restore destination"):
        safe_restore_path(dest, "link-out/secret.txt")


def test_gc_removes_stale_tmp_per_policy(repo: Repository):
    """U-Test-25: stale tmp blobs are removed by gc (E2 policy)."""
    stale = repo.tmp_dir / ".blob.stale.tmp"
    stale.write_bytes(b"leftover")
    old = time.time() - DEFAULT_TMP_MAX_AGE_SECONDS - 60
    os.utime(stale, (old, old))

    result = repo.gc()
    assert not stale.exists()
    assert str(stale) in result.removed_tmp_files
