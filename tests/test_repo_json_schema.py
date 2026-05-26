"""Tests for repo.json schema and repository inspection commands."""

from __future__ import annotations

import json
from pathlib import Path


from backup_tool.repo_metadata import (
    CHUNKING,
    HASH_ALGORITHM,
    OBJECT_LAYOUT,
    REPO_VERSION,
    STORAGE,
    default_repo_metadata,
)
from backup_tool.repository import Repository


REPO_JSON_KEYS = frozenset(
    {
        "version",
        "created_at",
        "hash_algorithm",
        "storage",
        "object_layout",
        "chunking",
    }
)


def test_repo_json_schema_keys(repo_path: Path):
    Repository.init(repo_path)
    metadata = json.loads((repo_path / "repo.json").read_text(encoding="utf-8"))
    assert set(metadata) == REPO_JSON_KEYS
    assert metadata["version"] == REPO_VERSION
    assert metadata["hash_algorithm"] == HASH_ALGORITHM
    assert metadata["storage"] == STORAGE
    assert metadata["object_layout"] == OBJECT_LAYOUT
    assert metadata["chunking"] == CHUNKING


def test_default_repo_metadata_matches_written_schema():
    assert set(default_repo_metadata()) == REPO_JSON_KEYS


def test_repo_info_reports_counts(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    info = repo.repo_info()
    assert set(info.metadata) == REPO_JSON_KEYS
    assert info.snapshot_count == 1
    assert info.object_count >= 1
    assert info.last_backup_at is not None


def test_show_snapshot_returns_manifest(repo: Repository, source_dir: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    manifest = repo.show_snapshot("latest")
    assert "a.txt" in manifest.files
    assert manifest.version == 1
    assert manifest.skipped == []
