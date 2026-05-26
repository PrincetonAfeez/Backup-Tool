"""Shared pytest fixtures and helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backup_tool.repository import Repository
from backup_tool.snapshot_engine import SnapshotEngine


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "source"
    directory.mkdir()
    return directory


@pytest.fixture
def repo_path(tmp_path: Path) -> Path:
    return tmp_path / "repo"


@pytest.fixture
def initialized_repo(repo_path: Path) -> Path:
    Repository.init(repo_path)
    return repo_path


@pytest.fixture
def repo(initialized_repo: Path) -> Repository:
    return Repository(initialized_repo)


@pytest.fixture
def populated_source(source_dir: Path) -> Path:
    (source_dir / "notes").mkdir()
    (source_dir / "notes" / "todo.txt").write_text("one\n", encoding="utf-8")
    (source_dir / "same-a.txt").write_text("same", encoding="utf-8")
    (source_dir / "same-b.txt").write_text("same", encoding="utf-8")
    return source_dir


@pytest.fixture
def repo_with_snapshot(repo: Repository, populated_source: Path) -> Repository:
    repo.backup(populated_source)
    return repo


def symlinks_supported() -> bool:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "target.txt"
        link = root / "link.txt"
        target.write_text("linked", encoding="utf-8")
        try:
            link.symlink_to("target.txt")
        except (OSError, NotImplementedError):
            return False
        return link.is_symlink()


symlink_required = pytest.mark.skipif(
    not symlinks_supported(),
    reason="symlink creation is not supported on this platform",
)

REAL_READ_REGULAR_FILE = SnapshotEngine._read_regular_file


def read_regular_file_with_skip(engine: SnapshotEngine, path: Path, dry_run: bool):
    if path.name == "skip-me.txt":
        return None, 0, 0
    return REAL_READ_REGULAR_FILE(engine, path, dry_run=dry_run)


@pytest.fixture
def skip_read_patch(monkeypatch):
    monkeypatch.setattr(SnapshotEngine, "_read_regular_file", read_regular_file_with_skip)
