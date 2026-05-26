"""Snapshot classification and diff helpers."""

from __future__ import annotations

from dataclasses import dataclass

from backup_tool.manifest import FileEntry, Manifest


@dataclass(frozen=True)
class DiffResult:
    added: list[str]
    changed: list[str]
    deleted: list[str]
    unchanged: list[str]

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.changed or self.deleted)


def classify_entries(
    current: dict[str, FileEntry],
    previous: dict[str, FileEntry] | None,
) -> DiffResult:
    previous = previous or {}
    current_paths = set(current)
    previous_paths = set(previous)

    added = sorted(current_paths - previous_paths)
    deleted = sorted(previous_paths - current_paths)
    changed: list[str] = []
    unchanged: list[str] = []

    for path in sorted(current_paths & previous_paths):
        if current[path].identity() == previous[path].identity():
            unchanged.append(path)
        else:
            changed.append(path)

    return DiffResult(added=added, changed=changed, deleted=deleted, unchanged=unchanged)


def diff_manifests(a: Manifest, b: Manifest) -> DiffResult:
    return classify_entries(b.files, a.files)
