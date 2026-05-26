"""Backup and restore orchestration."""

from __future__ import annotations

import fnmatch
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from secrets import token_hex

from backup_tool.chunking import restore_file_content, store_file
from backup_tool.diff import DiffResult, classify_entries
from backup_tool.errors import HashError, ManifestError, RestoreError, StoreError
from backup_tool.manifest import FileEntry, Manifest
from backup_tool.object_store import ObjectStore
from backup_tool.paths import normalize_manifest_path, safe_restore_path, source_relative_path


@dataclass(frozen=True)
class SkippedItem:
    path: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


@dataclass
class SnapshotResult:
    manifest: Manifest | None
    diff: DiffResult
    committed: bool
    dry_run: bool
    skipped: list[SkippedItem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
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
    warnings: list[str] = field(default_factory=list)


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

        for file_path, manifest_path, is_symlink in self._walk_source(source, excludes):
            if is_symlink:
                try:
                    stat_result = file_path.lstat()
                    files[manifest_path] = FileEntry(
                        type="symlink",
                        target=os.readlink(file_path),
                        mode=stat_result.st_mode,
                    )
                except OSError as exc:
                    item = SkippedItem(manifest_path, f"could not read symlink: {exc}")
                    skipped.append(item)
                    errors.append(item.reason)
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
        stats = {
            "file_count": len(files),
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

        now = datetime.now(UTC)
        manifest = Manifest(
            snapshot_id=self._new_snapshot_id(now),
            created_at=now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
            source=str(source),
            status=status,
            stats=stats,
            files=files,
            skipped=[item.to_dict() for item in skipped],
        )

        return SnapshotResult(
            manifest=manifest,
            diff=diff,
            committed=False,
            dry_run=dry_run,
            skipped=skipped,
            errors=errors,
        )

    def restore_snapshot(
        self,
        snapshot: Manifest,
        destination: Path,
        file_path: str | None = None,
        force: bool = False,
    ) -> RestoreResult:
        destination = destination.resolve()
        selected = self._select_restore_entries(snapshot, file_path)
        if not selected:
            raise RestoreError("No files matched restore request")

        self._check_destination(destination, force)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(tempfile.mkdtemp(prefix=f".restore-{snapshot.snapshot_id}.", dir=destination.parent))

        restored_files = 0
        restored_symlinks = 0
        warnings: list[str] = []

        try:
            for manifest_path, entry in selected.items():
                target = safe_restore_path(temp_path, manifest_path)
                target.parent.mkdir(parents=True, exist_ok=True)

                if entry.type == "file":
                    if not entry.hash:
                        raise RestoreError(f"File entry missing hash: {manifest_path}")
                    try:
                        restore_file_content(self.object_store, entry, target)
                    except (HashError, StoreError) as exc:
                        raise RestoreError(str(exc)) from exc
                    self._restore_metadata(target, entry, warnings)
                    restored_files += 1
                elif entry.type == "symlink":
                    if entry.target is None:
                        raise RestoreError(f"Symlink entry missing target: {manifest_path}")
                    try:
                        os.symlink(entry.target, target)
                        restored_symlinks += 1
                    except OSError as exc:
                        warnings.append(f"Could not restore symlink {manifest_path}: {exc}")
                else:
                    raise RestoreError(f"Unsupported entry type: {entry.type}")

            if destination.exists():
                if destination.is_dir():
                    destination.rmdir()
                else:
                    destination.unlink()
            os.replace(temp_path, destination)
        except Exception:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise

        return RestoreResult(
            snapshot_id=snapshot.snapshot_id,
            destination=destination,
            restored_files=restored_files,
            restored_symlinks=restored_symlinks,
            warnings=warnings,
        )

    def _read_regular_file(self, path: Path, dry_run: bool) -> tuple[FileEntry | None, int, int]:
        attempts = 2
        for _ in range(attempts):
            before = path.stat()
            try:
                stored = store_file(self.object_store, path, dry_run=dry_run)
            except (HashError, StoreError) as exc:
                raise exc
            after = path.stat()

            if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
                return (
                    FileEntry(
                        type="file",
                        hash=stored.hash_hex,
                        size=stored.size,
                        mtime=after.st_mtime,
                        mode=after.st_mode,
                        chunks=stored.chunks,
                    ),
                    stored.new_blob_count,
                    stored.bytes_stored,
                )

        return None, 0, 0

    def _walk_source(
        self,
        source: Path,
        excludes: list[str],
    ) -> list[tuple[Path, str, bool]]:
        found: list[tuple[Path, str, bool]] = []

        for root, dirnames, filenames in os.walk(source, topdown=True, followlinks=False):
            root_path = Path(root)

            kept_dirs: list[str] = []
            for dirname in dirnames:
                path = root_path / dirname
                manifest_path = source_relative_path(source, path)
                if self._is_excluded(manifest_path, excludes):
                    continue
                if path.is_symlink():
                    found.append((path, manifest_path, True))
                    continue
                kept_dirs.append(dirname)
            dirnames[:] = kept_dirs

            for filename in filenames:
                path = root_path / filename
                manifest_path = source_relative_path(source, path)
                if self._is_excluded(manifest_path, excludes):
                    continue
                found.append((path, manifest_path, path.is_symlink()))

        return sorted(found, key=lambda item: item[1])

    def _is_excluded(self, manifest_path: str, patterns: list[str]) -> bool:
        name = PurePosixPath(manifest_path).name
        for pattern in patterns:
            normalized = normalize_manifest_path(pattern) if pattern not in {"*", "**"} else pattern
            if fnmatch.fnmatch(manifest_path, normalized):
                return True
            if fnmatch.fnmatch(name, normalized):
                return True
            if manifest_path.startswith(normalized.rstrip("/") + "/"):
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
        if not destination.exists():
            return

        if destination.is_dir():
            has_children = any(destination.iterdir())
            if has_children and not force:
                raise RestoreError(f"Destination is not empty: {destination}")
            if not has_children:
                return
        elif not force:
            raise RestoreError(f"Destination already exists: {destination}")

        if destination.exists() and force:
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()

    def _restore_metadata(self, path: Path, entry: FileEntry, warnings: list[str]) -> None:
        if entry.mode is not None:
            try:
                os.chmod(path, entry.mode)
            except OSError as exc:
                warnings.append(f"Could not restore mode for {path}: {exc}")
        if entry.mtime is not None:
            try:
                os.utime(path, (entry.mtime, entry.mtime))
            except OSError as exc:
                warnings.append(f"Could not restore mtime for {path}: {exc}")

    def _new_snapshot_id(self, now: datetime) -> str:
        stamp = now.strftime("%Y-%m-%dT%H-%M-%S-%fZ")
        return f"{stamp}_{token_hex(4)}"
