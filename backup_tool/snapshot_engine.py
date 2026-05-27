"""Backup and restore orchestration."""

from __future__ import annotations

import fnmatch
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from secrets import token_hex

from backup_tool.chunking import hash_file_content, restore_file_content, store_file
from backup_tool.diff import DiffResult, classify_entries
from backup_tool.errors import HashError, ManifestError, RestoreError, StoreError
from backup_tool.manifest import FileEntry, Manifest
from backup_tool.metadata import restore_entry_metadata
from backup_tool.object_store import ObjectStore
from backup_tool.staging import staging_snapshot_id
from backup_tool.paths import (
    assert_safe_symlink_target,
    normalize_manifest_path,
    safe_restore_path,
    source_relative_path,
    validate_exclude_pattern,
)

SkipPredicate = Callable[[Path, str], str | None]


@dataclass(frozen=True)
class SkippedItem:
    path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass
class SnapshotResult:
    manifest: Manifest | None
    diff: DiffResult | None
    committed: bool
    dry_run: bool
    skipped: list[SkippedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stale_lock_cleared_pid: int | None = None

    @property
    def status(self) -> str:
        if self.manifest is None:
            return "aborted"
        return self.manifest.status


@dataclass
class RestoreResult:
    snapshot_id: str
    destination: Path
    restored_files: int
    restored_symlinks: int
    restored_directories: int = 0
    failed_symlinks: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "partial" if self.failed_symlinks else "complete"


class SnapshotEngine:
    def __init__(self, object_store: ObjectStore):
        self.object_store = object_store

    def build_snapshot(
        self,
        source: Path,
        previous_manifest: Manifest | None,
        excludes: list[str] | None = None,
        dry_run: bool = False,
        strict: bool = False,
        skip_predicate: SkipPredicate | None = None,
    ) -> SnapshotResult:
        source = source.resolve()
        if not source.exists() or not source.is_dir():
            raise ManifestError(f"Source must be an existing directory: {source}")

        excludes = [pattern.replace("\\", "/") for pattern in (excludes or [])]
        files: dict[str, FileEntry] = {}
        skipped: list[SkippedItem] = []
        errors: list[str] = []
        new_blobs = 0
        new_bytes_stored = 0
        total_bytes_scanned = 0

        now = datetime.now(UTC)
        snapshot_id = staging_snapshot_id(now, token_hex(4))
        staging_active = not dry_run
        if staging_active:
            self.object_store.begin_staging(snapshot_id)

        try:
            walk_entries, walk_skipped = self._walk_source(source, excludes)
            skipped.extend(walk_skipped)
            errors.extend(item.reason for item in walk_skipped)

            for file_path, manifest_path, entry_kind in walk_entries:
                if entry_kind == "directory":
                    try:
                        stat_result = file_path.lstat()
                        files[manifest_path] = FileEntry(
                            type="directory",
                            mode=stat.S_IMODE(stat_result.st_mode),
                            mtime=stat_result.st_mtime_ns / 1_000_000_000,
                        )
                    except OSError as exc:
                        item = SkippedItem(manifest_path, f"could not read directory: {exc}")
                        skipped.append(item)
                        errors.append(item.reason)
                    continue

                if entry_kind == "symlink":
                    try:
                        stat_result = file_path.lstat()
                        files[manifest_path] = FileEntry(
                            type="symlink",
                            target=os.readlink(file_path),
                            mode=stat.S_IMODE(stat_result.st_mode),
                            is_dir_symlink=file_path.is_dir(),
                        )
                    except OSError as exc:
                        item = SkippedItem(manifest_path, f"could not read symlink: {exc}")
                        skipped.append(item)
                        errors.append(item.reason)
                    continue

                if skip_predicate is not None:
                    skip_reason = skip_predicate(file_path, manifest_path)
                    if skip_reason is not None:
                        item = SkippedItem(manifest_path, skip_reason)
                        skipped.append(item)
                        errors.append(skip_reason)
                        continue

                try:
                    entry, stored_new_blob, bytes_stored = self._read_regular_file(
                        file_path,
                        dry_run=dry_run,
                    )
                except (HashError, StoreError, OSError) as exc:
                    item = SkippedItem(manifest_path, str(exc))
                    skipped.append(item)
                    errors.append(str(exc))
                    continue

                if entry is None:
                    item = SkippedItem(manifest_path, "file changed while being read")
                    skipped.append(item)
                    errors.append(item.reason)
                    continue

                files[manifest_path] = entry
                total_bytes_scanned += entry.size or 0
                if stored_new_blob:
                    new_blobs += stored_new_blob
                    new_bytes_stored += bytes_stored

            if skipped and strict:
                diff = classify_entries(files, previous_manifest.files if previous_manifest else None)
                return SnapshotResult(
                    manifest=None,
                    diff=diff,
                    committed=False,
                    dry_run=dry_run,
                    skipped=skipped,
                    errors=errors,
                )

            diff = classify_entries(files, previous_manifest.files if previous_manifest else None)
            status = "dry-run" if dry_run else ("partial" if skipped else "complete")
            regular_file_count = sum(1 for entry in files.values() if entry.type == "file")
            directory_count = sum(1 for entry in files.values() if entry.type == "directory")
            symlink_count = sum(1 for entry in files.values() if entry.type == "symlink")
            stats = {
                "entry_count": len(files),
                "regular_file_count": regular_file_count,
                "directory_count": directory_count,
                "symlink_count": symlink_count,
                "total_bytes_scanned": total_bytes_scanned,
                "new_bytes_stored": new_bytes_stored,
                "new_blobs": new_blobs,
                "new_files": len(diff.added),
                "changed_files": len(diff.changed),
                "deleted_files": len(diff.deleted),
                "unchanged_files": len(diff.unchanged),
                "skipped_files": len(skipped),
                "errors": len(errors),
            }

            manifest = Manifest(
                snapshot_id=snapshot_id,
                created_at=now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                source=str(source),
                status=status,
                stats=stats,
                files=files,
                skipped=[item.to_dict() for item in skipped],
            )

            if staging_active:
                self.object_store.promote_staging(snapshot_id)

            return SnapshotResult(
                manifest=manifest,
                diff=diff,
                committed=False,
                dry_run=dry_run,
                skipped=skipped,
                errors=errors,
            )
        finally:
            if staging_active:
                self.object_store.discard_staging(snapshot_id)

    def restore_snapshot(
        self,
        snapshot: Manifest,
        destination: Path,
        file_path: str | None = None,
        force: bool = False,
        safe_symlinks: bool = False,
    ) -> RestoreResult:
        selected = self._select_restore_entries(snapshot, file_path)
        if not selected and file_path is not None:
            raise RestoreError("No files matched restore request")

        self._check_destination(destination, force)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(
            tempfile.mkdtemp(
                prefix=f".restore-{snapshot.snapshot_id}.",
                dir=destination.parent,
            )
        )

        restored_files = 0
        restored_symlinks = 0
        restored_directories = 0
        failed_symlinks = 0
        warnings: list[str] = []
        old_path: Path | None = None

        try:
            directory_entries = [
                (path, entry) for path, entry in selected.items() if entry.type == "directory"
            ]
            file_entries = [
                (path, entry) for path, entry in selected.items() if entry.type == "file"
            ]
            symlink_entries = [
                (path, entry) for path, entry in selected.items() if entry.type == "symlink"
            ]
            other_types = {
                entry.type
                for entry in selected.values()
                if entry.type not in {"directory", "file", "symlink"}
            }
            if other_types:
                raise RestoreError(f"Unsupported entry type: {other_types.pop()}")

            for manifest_path, entry in directory_entries:
                target = safe_restore_path(temp_path, manifest_path)
                target.mkdir(parents=True, exist_ok=True)
                restored_directories += 1

            for manifest_path, entry in file_entries:
                target = safe_restore_path(temp_path, manifest_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                if not entry.hash:
                    raise RestoreError(f"File entry missing hash: {manifest_path}")
                try:
                    restore_file_content(self.object_store, entry, target)
                except (HashError, StoreError) as exc:
                    raise RestoreError(str(exc)) from exc
                restore_entry_metadata(target, entry, warnings)
                restored_files += 1

            for manifest_path, entry in symlink_entries:
                target = safe_restore_path(temp_path, manifest_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                if entry.target is None:
                    raise RestoreError(f"Symlink entry missing target: {manifest_path}")
                if safe_symlinks:
                    assert_safe_symlink_target(entry.target, manifest_path=manifest_path)
                try:
                    target_is_directory = bool(entry.is_dir_symlink) if os.name == "nt" else False
                    os.symlink(entry.target, target, target_is_directory=target_is_directory)
                    restored_symlinks += 1
                except OSError as exc:
                    failed_symlinks += 1
                    warnings.append(f"Could not restore symlink {manifest_path}: {exc}")

            for manifest_path, entry in sorted(
                directory_entries,
                key=lambda item: item[0].count("/"),
                reverse=True,
            ):
                target = safe_restore_path(temp_path, manifest_path)
                restore_entry_metadata(target, entry, warnings)

            if failed_symlinks and os.path.lexists(destination):
                raise RestoreError(
                    "Restore is partial; refusing to replace existing destination"
                )

            if os.path.lexists(destination):
                while True:
                    old_path = destination.parent / (
                        f".restore-old-{snapshot.snapshot_id}-{token_hex(4)}"
                    )
                    if not old_path.exists():
                        break
                os.replace(destination, old_path)
            os.replace(temp_path, destination)
            temp_path = destination
        except Exception:
            if temp_path.exists() and temp_path != destination:
                shutil.rmtree(temp_path, ignore_errors=True)
            if old_path is not None and old_path.exists() and not os.path.lexists(destination):
                os.replace(old_path, destination)
            raise
        finally:
            if old_path is not None and old_path.exists():
                if old_path.is_symlink():
                    old_path.unlink(missing_ok=True)
                elif old_path.is_dir():
                    shutil.rmtree(old_path, ignore_errors=True)
                else:
                    old_path.unlink(missing_ok=True)

        return RestoreResult(
            snapshot_id=snapshot.snapshot_id,
            destination=destination,
            restored_files=restored_files,
            restored_symlinks=restored_symlinks,
            restored_directories=restored_directories,
            failed_symlinks=failed_symlinks,
            warnings=warnings,
        )

    def _read_regular_file(self, path: Path, dry_run: bool) -> tuple[FileEntry | None, int, int]:
        prev_hash: str | None = None
        max_attempts = 4
        for _ in range(max_attempts):
            before = path.stat()
            hashed = hash_file_content(path)
            after = path.stat()

            stat_stable = (
                before.st_size == after.st_size
                and before.st_mtime_ns == after.st_mtime_ns
            )
            if not stat_stable:
                prev_hash = None
                continue

            if prev_hash is not None and prev_hash == hashed.hash_hex:
                stored = store_file(self.object_store, path, dry_run=dry_run)
                if stored.hash_hex != hashed.hash_hex or stored.size != hashed.size:
                    prev_hash = None
                    continue
                try:
                    final_stat = path.stat()
                except OSError:
                    prev_hash = None
                    continue
                if (
                    final_stat.st_size != after.st_size
                    or final_stat.st_mtime_ns != after.st_mtime_ns
                ):
                    prev_hash = None
                    continue
                return (
                    FileEntry(
                        type="file",
                        hash=stored.hash_hex,
                        size=stored.size,
                        mtime=final_stat.st_mtime_ns / 1_000_000_000,
                        mode=stat.S_IMODE(final_stat.st_mode),
                        chunks=stored.chunks,
                    ),
                    stored.new_blob_count,
                    stored.bytes_stored,
                )

            prev_hash = hashed.hash_hex

        return None, 0, 0

    def _walk_source(
        self,
        source: Path,
        excludes: list[str],
    ) -> tuple[list[tuple[Path, str, str]], list[SkippedItem]]:
        found: list[tuple[Path, str, str]] = []
        skipped: list[SkippedItem] = []

        def on_walk_error(exc: OSError) -> None:
            failed = getattr(exc, "filename", None)
            if failed:
                failed_path = Path(failed)
                try:
                    manifest_path = source_relative_path(source, failed_path)
                except ManifestError:
                    manifest_path = failed_path.name
            else:
                manifest_path = "."
            skipped.append(
                SkippedItem(manifest_path, f"could not scan directory: {exc}"),
            )

        for root, dirnames, filenames in os.walk(
            source,
            topdown=True,
            followlinks=False,
            onerror=on_walk_error,
        ):
            root_path = Path(root)

            if root_path != source:
                manifest_path = source_relative_path(source, root_path)
                if not self._is_excluded(manifest_path, excludes):
                    found.append((root_path, manifest_path, "directory"))

            kept_dirs: list[str] = []
            for dirname in dirnames:
                path = root_path / dirname
                manifest_path = source_relative_path(source, path)
                if self._is_excluded(manifest_path, excludes):
                    continue
                try:
                    stat_result = path.lstat()
                except OSError as exc:
                    skipped.append(
                        SkippedItem(manifest_path, f"could not stat directory: {exc}"),
                    )
                    continue
                if path.is_symlink():
                    found.append((path, manifest_path, "symlink"))
                    continue
                if not stat.S_ISDIR(stat_result.st_mode):
                    skipped.append(
                        SkippedItem(manifest_path, "unsupported file type"),
                    )
                    continue
                kept_dirs.append(dirname)
            dirnames[:] = kept_dirs

            for filename in filenames:
                path = root_path / filename
                manifest_path = source_relative_path(source, path)
                if self._is_excluded(manifest_path, excludes):
                    continue
                try:
                    stat_result = path.lstat()
                except OSError as exc:
                    skipped.append(
                        SkippedItem(manifest_path, f"could not stat file: {exc}"),
                    )
                    continue
                if path.is_symlink():
                    found.append((path, manifest_path, "symlink"))
                elif stat.S_ISREG(stat_result.st_mode):
                    found.append((path, manifest_path, "file"))
                else:
                    skipped.append(
                        SkippedItem(manifest_path, "unsupported file type"),
                    )

        return sorted(found, key=lambda item: item[1]), skipped

    def _is_excluded(self, manifest_path: str, patterns: list[str]) -> bool:
        name = PurePosixPath(manifest_path).name
        for pattern in patterns:
            normalized = validate_exclude_pattern(pattern) if pattern not in {"*", "**"} else pattern
            if fnmatch.fnmatch(manifest_path, normalized):
                return True
            if "/" not in normalized and fnmatch.fnmatch(name, normalized):
                return True
            base = normalized.rstrip("/")
            if manifest_path == base or manifest_path.startswith(base + "/"):
                return True
        return False

    def _select_restore_entries(self, snapshot: Manifest, file_path: str | None) -> dict[str, FileEntry]:
        if file_path is None:
            return dict(snapshot.files)

        rel = normalize_manifest_path(file_path)
        selected = {
            path: entry
            for path, entry in snapshot.files.items()
            if path == rel or path.startswith(rel.rstrip("/") + "/")
        }
        return dict(sorted(selected.items()))

    def _check_destination(self, destination: Path, force: bool) -> None:
        if not os.path.lexists(destination):
            return

        if destination.is_symlink():
            if force:
                return
            raise RestoreError(f"Destination already exists: {destination}")

        if destination.is_dir():
            is_empty = not any(destination.iterdir())
            if is_empty:
                return
            if force:
                return
            raise RestoreError(f"Destination is not empty: {destination}")

        if force:
            return
        raise RestoreError(f"Destination already exists: {destination}")
