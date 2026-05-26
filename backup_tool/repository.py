"""User-facing repository API."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backup_tool.atomic import atomic_write_json
from backup_tool.chunking import file_blob_hashes, verify_file_content
from backup_tool.diff import DiffResult, diff_manifests
from backup_tool.errors import IntegrityError, ManifestError, RepositoryError
from backup_tool.lock import RepositoryLock
from backup_tool.manifest import Manifest, ManifestStore
from backup_tool.object_store import ObjectStore, StoreError
from backup_tool.snapshot_engine import RestoreResult, SnapshotEngine, SnapshotResult


REPO_VERSION = 1


@dataclass(frozen=True)
class SnapshotSummary:
    snapshot_id: str
    created_at: str
    source: str
    status: str
    file_count: int
    new_bytes_stored: int


@dataclass
class VerifyResult:
    ok: bool
    snapshot_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class CheckResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    snapshot_count: int = 0
    object_count: int = 0
    referenced_object_count: int = 0
    orphan_object_count: int = 0


@dataclass
class PruneResult:
    deleted_snapshots: list[str]
    kept_snapshots: list[str]
    dry_run: bool
    gc_result: GCResult | None = None


@dataclass
class GCResult:
    deleted_blobs: list[str]
    kept_blobs: list[str]
    dry_run: bool
    bytes_deleted: int = 0


class Repository:
    """A backup repository on disk."""

    def __init__(self, path: Path):
        self.path = path
        self.objects_dir = path / "objects"
        self.snapshots_dir = path / "snapshots"
        self.tmp_dir = path / "tmp"
        self.repo_json = path / "repo.json"
        self.lock_path = path / "lock"
        self.object_store = ObjectStore(self.objects_dir, self.tmp_dir)
        self.manifest_store = ManifestStore(self.snapshots_dir)
        self.engine = SnapshotEngine(self.object_store)

    @classmethod
    def init(cls, path: Path, break_lock: bool = False) -> "Repository":
        repo = cls(path)
        if repo.repo_json.exists():
            raise RepositoryError(f"Repository already exists: {repo.path}")
        repo.path.mkdir(parents=True, exist_ok=True)
        with RepositoryLock(repo.lock_path, break_lock=break_lock):
            repo.object_store.init()
            repo.manifest_store.init()
            repo.tmp_dir.mkdir(parents=True, exist_ok=True)
            metadata = {
                "version": REPO_VERSION,
                "created_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                "hash_algorithm": "sha256",
                "storage": "content-addressable",
                "object_layout": "sha256-prefix-2",
                "chunking": "fixed-1mb-blocks-above-threshold",
            }
            atomic_write_json(repo.repo_json, metadata)
        return repo

    def backup(
        self,
        source: Path,
        excludes: list[str] | None = None,
        dry_run: bool = False,
        strict: bool = False,
        break_lock: bool = False,
    ) -> SnapshotResult:
        self._ensure_initialized()
        excludes = self._with_repo_self_exclude(source, excludes or [])

        if dry_run:
            previous = self.manifest_store.latest()
            return self.engine.build_snapshot(source, previous, excludes=excludes, dry_run=True, strict=strict)

        with RepositoryLock(self.lock_path, break_lock=break_lock) as lock:
            previous = self.manifest_store.latest()
            result = self.engine.build_snapshot(source, previous, excludes=excludes, dry_run=False, strict=strict)
            result.stale_lock_cleared_pid = lock.cleared_stale_pid
            if result.manifest is None:
                return result
            self._ensure_manifest_blobs_exist(result.manifest)
            self.manifest_store.save(result.manifest)
            result.committed = True
            return result

    def list_snapshots(self) -> list[SnapshotSummary]:
        self._ensure_initialized()
        summaries: list[SnapshotSummary] = []
        for manifest in self.manifest_store.list_manifests():
            summaries.append(
                SnapshotSummary(
                    snapshot_id=manifest.snapshot_id,
                    created_at=manifest.created_at,
                    source=manifest.source,
                    status=manifest.status,
                    file_count=int(manifest.stats.get("file_count", len(manifest.files))),
                    new_bytes_stored=int(manifest.stats.get("new_bytes_stored", 0)),
                )
            )
        return summaries

    def restore(
        self,
        snapshot_id: str,
        destination: Path,
        file_path: str | None = None,
        force: bool = False,
        break_lock: bool = False,
    ) -> RestoreResult:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            manifest = self._resolve_snapshot(snapshot_id)
            return self.engine.restore_snapshot(manifest, destination, file_path=file_path, force=force)

    def diff(self, snapshot_a: str, snapshot_b: str) -> DiffResult:
        self._ensure_initialized()
        a = self._resolve_snapshot(snapshot_a)
        b = self._resolve_snapshot(snapshot_b)
        return diff_manifests(a, b)

    def verify(self, snapshot_id: str) -> VerifyResult:
        self._ensure_initialized()
        errors: list[str] = []
        warnings: list[str] = []

        try:
            manifest = self._resolve_snapshot(snapshot_id)
        except (ManifestError, RepositoryError) as exc:
            return VerifyResult(False, snapshot_id, [str(exc)], warnings)

        for path, entry in manifest.files.items():
            if entry.type != "file":
                continue
            if not entry.hash:
                errors.append(f"{path}: file entry missing hash")
                continue
            try:
                if not verify_file_content(self.object_store, entry):
                    errors.append(f"{path}: content verification failed for {entry.hash}")
            except (IntegrityError, StoreError) as exc:
                errors.append(f"{path}: {exc}")

        if manifest.status == "partial":
            warnings.append("snapshot is partial")

        return VerifyResult(not errors, manifest.snapshot_id, errors, warnings)

    def check(self) -> CheckResult:
        self._ensure_initialized()
        errors: list[str] = []
        warnings: list[str] = []
        referenced: set[str] = set()
        snapshot_count = 0

        try:
            metadata = json.loads(self.repo_json.read_text(encoding="utf-8"))
            if metadata.get("version") != REPO_VERSION:
                errors.append(f"Unsupported repo version: {metadata.get('version')}")
            if metadata.get("hash_algorithm") != "sha256":
                errors.append("Repository hash algorithm is not sha256")
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Invalid repo.json: {exc}")

        for path in self.manifest_store.list_paths():
            try:
                manifest = self.manifest_store.load_path(path)
            except ManifestError as exc:
                errors.append(str(exc))
                continue
            snapshot_count += 1
            for manifest_path, entry in manifest.files.items():
                if entry.type == "file":
                    if not entry.hash:
                        errors.append(f"{manifest.snapshot_id}:{manifest_path}: missing hash")
                        continue
                    for blob_hash in file_blob_hashes(entry):
                        referenced.add(blob_hash)
                    try:
                        if not verify_file_content(self.object_store, entry):
                            errors.append(f"{manifest.snapshot_id}:{manifest_path}: content verification failed")
                    except (IntegrityError, StoreError) as exc:
                        errors.append(f"{manifest.snapshot_id}:{manifest_path}: {exc}")

        malformed = self.object_store.iter_malformed_paths()
        for path in malformed:
            errors.append(f"Malformed object path: {path}")

        all_objects = set(self.object_store.iter_hashes())
        orphaned = all_objects - referenced
        if orphaned:
            warnings.append(f"{len(orphaned)} orphan blob(s) found")

        return CheckResult(
            ok=not errors,
            errors=errors,
            warnings=warnings,
            snapshot_count=snapshot_count,
            object_count=len(all_objects),
            referenced_object_count=len(referenced),
            orphan_object_count=len(orphaned),
        )

    def prune(
        self,
        keep: int,
        dry_run: bool = False,
        run_gc: bool = False,
        break_lock: bool = False,
    ) -> PruneResult:
        self._ensure_initialized()
        if keep < 0:
            raise RepositoryError("keep must be >= 0")

        with RepositoryLock(self.lock_path, break_lock=break_lock):
            manifests = self.manifest_store.list_manifests()
            to_delete = manifests[:-keep] if keep else manifests
            kept = manifests[-keep:] if keep else []

            if not dry_run:
                for manifest in to_delete:
                    self.manifest_store.path_for(manifest.snapshot_id).unlink(missing_ok=True)

            gc_result = self._gc_unlocked(dry_run=dry_run) if run_gc else None

            return PruneResult(
                deleted_snapshots=[manifest.snapshot_id for manifest in to_delete],
                kept_snapshots=[manifest.snapshot_id for manifest in kept],
                dry_run=dry_run,
                gc_result=gc_result,
            )

    def gc(self, dry_run: bool = False, break_lock: bool = False) -> GCResult:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            return self._gc_unlocked(dry_run=dry_run)

    def _gc_unlocked(self, dry_run: bool = False) -> GCResult:
        referenced: set[str] = set()
        for manifest in self.manifest_store.list_manifests():
            for entry in manifest.files.values():
                if entry.type == "file":
                    referenced.update(file_blob_hashes(entry))

        all_hashes = set(self.object_store.iter_hashes())
        garbage = sorted(all_hashes - referenced)
        kept = sorted(all_hashes & referenced)
        bytes_deleted = 0

        if not dry_run:
            for hash_hex in garbage:
                path = self.object_store.get_path(hash_hex)
                try:
                    bytes_deleted += path.stat().st_size
                except OSError:
                    pass
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
        )

    def _resolve_snapshot(self, snapshot_id: str) -> Manifest:
        if snapshot_id == "latest":
            latest = self.manifest_store.latest()
            if latest is None:
                raise RepositoryError("No snapshots found")
            return latest

        if snapshot_id.endswith(".json"):
            snapshot_id = snapshot_id[:-5]
        return self.manifest_store.load(snapshot_id)

    def _ensure_initialized(self) -> None:
        if not self.repo_json.exists():
            raise RepositoryError(f"Not a backup repository: {self.path}")
        if not self.objects_dir.exists() or not self.snapshots_dir.exists():
            raise RepositoryError(f"Repository is missing required directories: {self.path}")

    def _ensure_manifest_blobs_exist(self, manifest: Manifest) -> None:
        missing: list[str] = []
        for path, entry in manifest.files.items():
            if entry.type != "file":
                continue
            for blob_hash in file_blob_hashes(entry):
                if not self.object_store.exists(blob_hash):
                    missing.append(f"{path}:{blob_hash}")
        if missing:
            raise RepositoryError("Manifest references missing blobs: " + ", ".join(missing))

    def _with_repo_self_exclude(self, source: Path, excludes: list[str]) -> list[str]:
        try:
            repo_relative = self.path.resolve().relative_to(source.resolve())
        except ValueError:
            return list(excludes)

        if str(repo_relative) in ("", "."):
            return list(excludes)
        return [*excludes, repo_relative.as_posix()]
