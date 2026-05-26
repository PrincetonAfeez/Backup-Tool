"""Tests for backup_tool.diff."""

from backup_tool.diff import DiffResult, classify_entries, diff_manifests
from backup_tool.manifest import FileEntry, Manifest


def _file_entry(content_hash: str, chunks: tuple[str, ...] | None = None) -> FileEntry:
    return FileEntry(type="file", hash=content_hash, size=1, chunks=chunks)


def test_diff_result_has_changes_property():
    assert DiffResult([], [], [], []).has_changes is False
    assert DiffResult(["a"], [], [], []).has_changes is True
    assert DiffResult([], ["b"], [], []).has_changes is True
    assert DiffResult([], [], ["c"], []).has_changes is True


def test_classify_entries_detects_add_change_delete_unchanged():
    previous = {
        "keep.txt": _file_entry("hash-keep"),
        "old.txt": _file_entry("hash-old"),
    }
    current = {
        "keep.txt": _file_entry("hash-keep"),
        "old.txt": _file_entry("hash-new"),
        "new.txt": _file_entry("hash-new-file"),
    }
    result = classify_entries(current, previous)
    assert result.added == ["new.txt"]
    assert result.deleted == []
    assert result.changed == ["old.txt"]
    assert result.unchanged == ["keep.txt"]


def test_classify_entries_treats_chunk_changes_as_changed():
    previous = {"big.bin": _file_entry("h1", ("c1", "c2"))}
    current = {"big.bin": _file_entry("h2", ("c1", "c3"))}
    result = classify_entries(current, previous)
    assert result.changed == ["big.bin"]


def test_classify_entries_with_no_previous():
    current = {"a.txt": _file_entry("hash-a")}
    result = classify_entries(current, None)
    assert result.added == ["a.txt"]
    assert result.deleted == []


def test_diff_manifests_compares_two_manifests():
    manifest_a = Manifest(
        snapshot_id="a",
        created_at="t1",
        source="src",
        status="complete",
        stats={},
        files={"x.txt": _file_entry("h1")},
    )
    manifest_b = Manifest(
        snapshot_id="b",
        created_at="t2",
        source="src",
        status="complete",
        stats={},
        files={"x.txt": _file_entry("h2"), "y.txt": _file_entry("h3")},
    )
    result = diff_manifests(manifest_a, manifest_b)
    assert result.changed == ["x.txt"]
    assert result.added == ["y.txt"]
