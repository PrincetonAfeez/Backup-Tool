"""Tests for backup_tool.paths."""

from pathlib import Path

import pytest

from backup_tool.errors import ManifestError, RestoreError
from backup_tool.paths import (
    assert_safe_symlink_target,
    is_safe_symlink_target,
    normalize_manifest_path,
    safe_restore_path,
    source_relative_path,
    validate_exclude_pattern,
    validate_restore_file_path,
)


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


@pytest.mark.parametrize(
    "pattern",
    ["../secret", "..", "foo/../bar"],
)
def test_validate_exclude_pattern_rejects_unsafe(pattern: str):
    with pytest.raises(ManifestError, match="Unsafe exclude pattern"):
        validate_exclude_pattern(pattern)


def test_validate_exclude_pattern_accepts_safe_patterns():
    assert validate_exclude_pattern("*.tmp") == "*.tmp"
    assert validate_exclude_pattern("/etc") == "etc"


@pytest.mark.parametrize("pattern", ["*", "**"])
def test_validate_exclude_pattern_rejects_bare_wildcards(pattern: str):
    with pytest.raises(ManifestError, match="cannot be '\\*' or '\\*\\*'"):
        validate_exclude_pattern(pattern)


def test_validate_restore_file_path_rejects_empty_values():
    with pytest.raises(RestoreError, match="Invalid --file value"):
        validate_restore_file_path("")
    with pytest.raises(RestoreError, match="Invalid --file value"):
        validate_restore_file_path(".")


def test_validate_restore_file_path_normalizes():
    assert validate_restore_file_path("notes/todo.txt") == "notes/todo.txt"
    assert validate_restore_file_path(None) is None


def test_manifest_path_matches_exclude_pattern_is_path_aware():
    from backup_tool.paths import manifest_path_matches_exclude_pattern

    assert manifest_path_matches_exclude_pattern("dir/top.py", "dir/*.py")
    assert not manifest_path_matches_exclude_pattern("dir/sub/nested.py", "dir/*.py")


@pytest.mark.parametrize(
    "target,expected",
    [
        ("relative/path", True),
        ("./local", True),
        ("a:b", True),
        ("/etc/passwd", False),
        ("../outside", False),
        ("C:\\Windows\\System32", False),
        ("\\\\server\\share", False),
        ("", False),
    ],
)
def test_is_safe_symlink_target(target: str, expected: bool):
    assert is_safe_symlink_target(target) is expected


def test_assert_safe_symlink_target_accepts_relative():
    assert_safe_symlink_target("notes/readme.txt", manifest_path="link.txt")


@pytest.mark.parametrize(
    "target,match",
    [
        ("/etc/shadow", "Unsafe symlink target"),
        ("../escape", "Unsafe symlink target"),
        ("C:\\Windows\\System32", "Unsafe symlink target"),
    ],
)
def test_assert_safe_symlink_target_rejects_unsafe(target: str, match: str):
    with pytest.raises(RestoreError, match=match):
        assert_safe_symlink_target(target, manifest_path="link.txt")
