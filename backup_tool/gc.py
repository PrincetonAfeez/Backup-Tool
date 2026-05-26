"""Garbage collection for unreferenced content-addressed blobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backup_tool.chunking import file_blob_hashes
from backup_tool.manifest import Manifest

if TYPE_CHECKING:
    from backup_tool.repository import Repository


@dataclass
class GCResult:
    deleted_blobs: list[str]
    kept_blobs: list[str]
    dry_run: bool
    bytes_deleted: int = 0
    quarantined_malformed: list[str] = field(default_factory=list)
    removed_tmp_files: list[str] = field(default_factory=list)
    tmp_bytes_deleted: int = 0
    aggressive: bool = False


def gc_unlocked(
    repo: Repository,
    *,
    dry_run: bool = False,
    manifests: list[Manifest] | None = None,
    aggressive: bool = False,
) -> GCResult:
    quarantined_malformed: list[str] = []
    if aggressive:
        quarantine_dir = repo.tmp_dir / "quarantine"
        moved = repo.object_store.quarantine_malformed(quarantine_dir, dry_run=dry_run)
        quarantined_malformed = [
            f"{source} -> {hash_hex or 'unknown'}" for source, hash_hex in moved
        ]

    removed_tmp, tmp_bytes_deleted = repo.object_store.remove_stale_tmp_files(dry_run=dry_run)

    referenced: set[str] = set()
    source_manifests = manifests if manifests is not None else repo.manifest_store.list_manifests()
    for manifest in source_manifests:
        for entry in manifest.files.values():
            if entry.type == "file":
                referenced.update(file_blob_hashes(entry))

    on_disk = dict(repo.object_store.iter_blob_paths())
    all_hashes = set(on_disk)
    garbage = sorted(all_hashes - referenced)
    kept = sorted(all_hashes & referenced)
    bytes_deleted = 0

    for hash_hex in garbage:
        path = on_disk[hash_hex]
        try:
            bytes_deleted += path.stat().st_size
        except OSError:
            pass

    if not dry_run:
        for hash_hex in garbage:
            path = on_disk[hash_hex]
            path.unlink(missing_ok=True)
            try:
                path.parent.rmdir()
            except OSError:
                pass

    return GCResult(
        deleted_blobs=garbage,
        kept_blobs=kept,
        dry_run=dry_run,
        bytes_deleted=bytes_deleted,
        quarantined_malformed=quarantined_malformed,
        removed_tmp_files=removed_tmp,
        tmp_bytes_deleted=tmp_bytes_deleted,
        aggressive=aggressive,
    )
