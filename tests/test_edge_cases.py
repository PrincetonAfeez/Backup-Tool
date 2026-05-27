"""Regression tests for documented edge cases and behavior fixes."""

from __future__ import annotations

import io
import json
import os
import stat
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from backup_tool.cli import main
from backup_tool.diff import classify_entries
from backup_tool.manifest import FileEntry
from backup_tool.object_store import DEFAULT_TMP_MAX_AGE_SECONDS
from backup_tool.repository import Repository
from backup_tool.snapshot_engine import SnapshotEngine
from tests.conftest import manifest_hash, symlink_required


def _pyproject_version() -> str:
    import tomllib

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def test_version_matches_pyproject():
    from backup_tool import __version__

    assert __version__ == _pyproject_version()


def test_backup_absolute_exclude_excludes_matching_paths(
    repo: Repository,
    source_dir: Path,
):
    etc = source_dir / "etc"
    etc.mkdir()
    (etc / "hosts").write_text("127.0.0.1 localhost", encoding="utf-8")
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")

    result = repo.backup(source_dir, excludes=["/etc"])

    assert "keep.txt" in result.manifest.files
    assert "etc/hosts" not in result.manifest.files
    assert "etc" not in result.manifest.files


def test_backup_trailing_slash_exclude_omits_directory(
    repo: Repository,
    source_dir: Path,
):
    build = source_dir / "build"
    build.mkdir()
    (build / "artifact.txt").write_text("built", encoding="utf-8")
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")

    result = repo.backup(source_dir, excludes=["build/"])

    assert "keep.txt" in result.manifest.files
    assert "build" not in result.manifest.files
    assert "build/artifact.txt" not in result.manifest.files


def test_classify_entries_treats_metadata_only_changes_as_unchanged():
    shared_hash = manifest_hash("same-bytes")
    previous = {
        "doc.txt": FileEntry(
            type="file",
            hash=shared_hash,
            size=4,
            mode=0o644,
            mtime=1.0,
        ),
    }
    current = {
        "doc.txt": FileEntry(
            type="file",
            hash=shared_hash,
            size=4,
            mode=0o600,
            mtime=99.0,
        ),
    }

    result = classify_entries(current, previous)

    assert result.unchanged == ["doc.txt"]
    assert result.changed == []


@pytest.mark.skipif(os.name == "nt", reason="Unix file modes are not portable on Windows")
def test_backup_mode_only_change_is_unchanged_in_diff(
    repo: Repository,
    source_dir: Path,
):
    path = source_dir / "same.txt"
    path.write_text("same", encoding="utf-8")
    repo.backup(source_dir)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    second = repo.backup(source_dir)

    assert second.diff is not None
    assert "same.txt" in second.diff.unchanged
    assert "same.txt" not in second.diff.changed


def test_restore_applies_directory_mtime_after_children(
    engine: SnapshotEngine,
    source_dir: Path,
    tmp_path: Path,
):
    nested = source_dir / "nested"
    nested.mkdir()
    (nested / "file.txt").write_text("inside", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    dir_entry = manifest.files["nested"]
    assert dir_entry.mtime is not None

    destination = tmp_path / "restore"
    engine.restore_snapshot(manifest, destination)

    restored_mtime = (destination / "nested").stat().st_mtime
    assert abs(restored_mtime - dir_entry.mtime) < 0.002


@symlink_required
def test_restore_force_over_symlink_destination_preserves_target_tree(
    engine: SnapshotEngine,
    source_dir: Path,
    tmp_path: Path,
):
    (source_dir / "a.txt").write_text("backup", encoding="utf-8")
    manifest = engine.build_snapshot(source_dir, None).manifest
    real_dir = tmp_path / "real_dir"
    real_dir.mkdir()
    marker = real_dir / "marker.txt"
    marker.write_text("stay", encoding="utf-8")
    destination = tmp_path / "restore_link"
    destination.symlink_to(real_dir, target_is_directory=True)

    engine.restore_snapshot(manifest, destination, force=True)

    assert marker.read_text(encoding="utf-8") == "stay"
    assert (destination / "a.txt").read_text(encoding="utf-8") == "backup"


def test_cli_show_stdout_is_valid_json_only(
    repo: Repository,
    source_dir: Path,
    repo_path: Path,
):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        assert main(["show", "latest", "--repo", str(repo_path)]) == 0

    payload = json.loads(stdout.getvalue())
    assert payload["version"] == 1
    assert "a.txt" in payload["files"]
    assert "snapshot=" in stderr.getvalue()
    assert not stdout.getvalue().startswith("snapshot=")


@symlink_required
def test_force_restore_aborts_when_symlinks_fail_over_existing_destination(
    engine: SnapshotEngine,
    source_dir: Path,
    tmp_path: Path,
    monkeypatch,
):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "link.txt").symlink_to("keep.txt")
    manifest = engine.build_snapshot(source_dir, None).manifest
    destination = tmp_path / "restore"
    destination.mkdir()
    (destination / "precious.txt").write_text("do not lose", encoding="utf-8")

    def fail_symlink(*_args, **_kwargs):
        raise OSError("symlink denied")

    monkeypatch.setattr("backup_tool.snapshot_engine.os.symlink", fail_symlink)

    with pytest.raises(RestoreError, match="refusing to replace existing destination"):
        engine.restore_snapshot(manifest, destination, force=True)

    assert (destination / "precious.txt").read_text(encoding="utf-8") == "do not lose"


def test_stale_staging_cleanup_via_gc_and_check(
    repo: Repository,
    source_dir: Path,
):
    """Orphan tmp/staging/<snapshot-id>/ dirs are reclaimed by gc --aggressive and check --repair."""

    repo.backup(source_dir)
    orphan = repo.object_store.staging_root("2026-01-01T00-00-00-000000Z_deadbeef")
    orphan.mkdir(parents=True)
    (orphan / "aa").mkdir(parents=True)
    (orphan / "aa" / "blob").write_bytes(b"staged")
    old = time.time() - DEFAULT_TMP_MAX_AGE_SECONDS - 60
    os.utime(orphan, (old, old))

    gc_result = repo.gc(aggressive=True)
    assert not orphan.exists()
    assert str(orphan) in gc_result.removed_tmp_files

    orphan = repo.object_store.staging_root("2026-01-01T00-00-00-000000Z_cafebabe")
    orphan.mkdir(parents=True)
    os.utime(orphan, (old, old))
    check_result = repo.check(repair=True)
    assert not orphan.exists()
    assert check_result.repaired is True
    assert any("orphan staging" in warning for warning in check_result.warnings)
