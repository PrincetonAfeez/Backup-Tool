"""Tests for backup_tool.paths."""

from pathlib import Path

import pytest

from backup_tool.errors import ManifestError
from backup_tool.paths import normalize_manifest_path, safe_restore_path, source_relative_path


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("docs/readme.txt", "docs/readme.txt"),
        ("docs\\readme.txt", "docs/readme.txt"),
        ("file.txt", "file.txt"),
    ],
)
def test_normalize_manifest_path_accepts_safe_paths(raw, expected):
    assert normalize_manifest_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", ".", "../escape.txt", "/absolute.txt", "foo/../bar"],
)
def test_normalize_manifest_path_rejects_unsafe_paths(raw):
    with pytest.raises(ManifestError):
        normalize_manifest_path(raw)


def test_source_relative_path_inside_source(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    child = source / "a" / "b.txt"
    child.parent.mkdir()
    child.write_text("x")
    assert source_relative_path(source, child) == "a/b.txt"


def test_source_relative_path_outside_source_raises(tmp_path: Path):
    source = tmp_path / "source"
    outside = tmp_path / "outside.txt"
    source.mkdir()
    outside.write_text("x")
    with pytest.raises(ManifestError, match="not inside source"):
        source_relative_path(source, outside)


def test_safe_restore_path_inside_destination(tmp_path: Path):
    dest = tmp_path / "restore"
    dest.mkdir()
    target = safe_restore_path(dest, "notes/todo.txt")
    assert target == (dest / "notes" / "todo.txt").resolve()


def test_safe_restore_path_rejects_unsafe_manifest_path(tmp_path: Path):
    dest = tmp_path / "restore"
    dest.mkdir()
    with pytest.raises(ManifestError, match="Unsafe manifest path"):
        safe_restore_path(dest, "../outside.txt")
