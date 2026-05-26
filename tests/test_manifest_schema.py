"""Tests for manifest schema validation and trust-model mitigations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backup_tool.errors import ManifestError, RestoreError
from backup_tool.manifest import FileEntry, write_manifest_digest
from backup_tool.paths import is_safe_symlink_target
from backup_tool.repository import Repository
from backup_tool.snapshot_engine import SnapshotEngine
from tests.conftest import manifest_hash, symlink_required


def test_verify_flags_symlink_missing_target(repo: Repository, source_dir: Path):
    repo.backup(source_dir)
    manifest_path = repo.manifest_store.path_for(repo.manifest_store.latest().snapshot_id)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["files"]["bad-link"] = {"type": "symlink", "target": ""}
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    write_manifest_digest(manifest_path)
    result = repo.verify("latest")
    assert result.ok is False
    assert any("symlink entry missing target" in error for error in result.errors)


@symlink_required
def test_restore_safe_symlinks_rejects_absolute_target(engine: SnapshotEngine, source_dir: Path, tmp_path: Path):
    (source_dir / "target.txt").write_text("secret", encoding="utf-8")
    (source_dir / "link.txt").symlink_to("/etc/passwd")
    manifest = engine.build_snapshot(source_dir, None).manifest
    with pytest.raises(RestoreError, match="Unsafe symlink target"):
        engine.restore_snapshot(manifest, tmp_path / "restore", safe_symlinks=True)


def test_is_safe_symlink_target():
    assert is_safe_symlink_target("relative/path")
    assert not is_safe_symlink_target("/etc/shadow")
    assert not is_safe_symlink_target("../outside")
    assert not is_safe_symlink_target("C:\\Windows\\System32")


def test_manifest_from_dict_rejects_invalid_chunk_hash():
    payload = {
        "type": "file",
        "hash": manifest_hash("file"),
        "chunks": ["bad"],
    }
    with pytest.raises(ManifestError, match="Invalid SHA-256 hash length"):
        FileEntry.from_dict(payload)


def test_unsupported_entry_type_rejected_at_load():
    with pytest.raises(ManifestError, match="Unsupported file entry type"):
        FileEntry.from_dict({"type": "device"})
