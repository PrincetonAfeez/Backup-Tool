"""Tests for backup_tool.diff."""


from backup_tool.diff import DiffResult, classify_entries, diff_manifests
from backup_tool.manifest import FileEntry, Manifest
from tests.conftest import TEST_CREATED_AT, TEST_SNAPSHOT_ID_A, TEST_SNAPSHOT_ID_B, manifest_hash


def _file_entry(content_hash: str, chunks: tuple[str, ...] | None = None) -> FileEntry:
    return FileEntry(type="file", hash=manifest_hash(content_hash), size=1, chunks=chunks)


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
    previous = {"big.bin": _file_entry("h1", (manifest_hash("c1"), manifest_hash("c2")))}
    current = {"big.bin": _file_entry("h2", (manifest_hash("c1"), manifest_hash("c3")))}
    result = classify_entries(current, previous)
    assert result.changed == ["big.bin"]


def test_classify_entries_treats_mode_only_change_as_unchanged():
    shared_hash = manifest_hash("h")
    previous = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mode=0o644)}
    current = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mode=0o600)}
    result = classify_entries(current, previous)
    assert result.unchanged == ["a.txt"]
    assert result.changed == []


def test_classify_entries_with_no_previous():
    current = {"a.txt": _file_entry("hash-a")}
    result = classify_entries(current, None)
    assert result.added == ["a.txt"]
    assert result.deleted == []


def test_classify_entries_treats_mtime_only_change_as_unchanged():
    shared_hash = manifest_hash("h")
    previous = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mtime=1.0)}
    current = {"a.txt": FileEntry(type="file", hash=shared_hash, size=1, mtime=2.0)}
    result = classify_entries(current, previous)
    assert result.unchanged == ["a.txt"]
    assert result.changed == []


def test_diff_manifests_compares_two_manifests():
    manifest_a = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID_A,
        created_at=TEST_CREATED_AT,
        source="src",
        status="complete",
        stats={"entry_count": 1},
        files={"x.txt": _file_entry("h1")},
    )
    manifest_b = Manifest(
        snapshot_id=TEST_SNAPSHOT_ID_B,
        created_at="2026-01-02T00:00:00.000000Z",
        source="src",
        status="complete",
        stats={"entry_count": 2},
        files={"x.txt": _file_entry("h2"), "y.txt": _file_entry("h3")},
    )
    result = diff_manifests(manifest_a, manifest_b)
    assert result.changed == ["x.txt"]
    assert result.added == ["y.txt"]
