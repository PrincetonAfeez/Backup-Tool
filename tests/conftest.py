"""Shared pytest fixtures and helpers."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from backup_tool.object_store import ObjectStore
from backup_tool.repository import Repository
from backup_tool.snapshot_engine import SkipPredicate, SnapshotEngine


@pytest.fixture
def engine(tmp_path: Path) -> SnapshotEngine:
    objects_dir = tmp_path / "objects"
    tmp_dir = tmp_path / "tmp"
    store = ObjectStore(objects_dir, tmp_dir)
    store.init()
    return SnapshotEngine(store)


def manifest_hash(label: str) -> str:
    """Return a valid SHA-256 hex digest derived from a test label."""

    return sha256(label.encode()).hexdigest()


TEST_SNAPSHOT_ID = "2026-01-01T00-00-00-000000Z_abcd1234"
TEST_SNAPSHOT_ID_A = "2026-01-01T00-00-00-000000Z_abcd1234"
TEST_SNAPSHOT_ID_B = "2026-01-02T00-00-00-000000Z_deadbeef"
TEST_CREATED_AT = "2026-01-01T00:00:00.000000Z"
MISSING_SNAPSHOT_ID = "2026-01-09T00-00-00-000000Z_cafebabe"


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


def skip_skip_me(_path: Path, _manifest_path: str) -> str | None:
    if _path.name == "skip-me.txt":
        return "file changed while being read"
    return None


@pytest.fixture
def skip_predicate() -> SkipPredicate:
    """Public skip seam for tests (see ``SnapshotEngine.build_snapshot``)."""
    return skip_skip_me
