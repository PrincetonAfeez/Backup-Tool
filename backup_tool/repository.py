"""User-facing repository API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from backup_tool.atomic import atomic_write_json, fsync_directory
from backup_tool.chunking import file_blob_hashes
from backup_tool.diff import DiffResult, diff_manifests
from backup_tool.errors import ManifestError, RepositoryError
from backup_tool.gc import GCResult, gc_unlocked
from backup_tool.lock import RepositoryLock
from backup_tool.manifest import Manifest, ManifestStore
from backup_tool.object_store import ObjectStore
from backup_tool.paths import validate_exclude_pattern
from backup_tool.repo_metadata import default_repo_metadata, validate_repo_metadata
from backup_tool.snapshot_engine import RestoreResult, SnapshotEngine, SnapshotResult, SkipPredicate
from backup_tool.verify import CheckResult, VerifyResult, check_repository, verify_manifest


REPO_VERSION = 1  # re-export for tests and callers


@dataclass(frozen=True)
class SnapshotSummary:
    snapshot_id: str
    created_at: str
    source: str
    status: str
    file_count: int
    new_bytes_stored: int


@dataclass(frozen=True)
class RepoInfo:
    metadata: dict[str, object]
    snapshot_count: int
    object_count: int
    last_backup_at: str | None


@dataclass
class PruneResult:
    deleted_snapshots: list[str]
    kept_snapshots: list[str]
    dry_run: bool
    gc_result: GCResult | None = None


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
    def init(
        cls,
        path: Path,
        break_lock: bool = False,
        allow_nonempty: bool = False,
    ) -> "Repository":
        repo = cls(path)
        if repo.repo_json.exists():
            raise RepositoryError(f"Repository already exists: {repo.path}")
        if repo.path.exists():
            try:
                existing = list(repo.path.iterdir())
            except OSError as exc:
                raise RepositoryError(f"Cannot access {repo.path}: {exc}") from exc
            if existing and not allow_nonempty:
                raise RepositoryError(
                    f"Directory is not empty: {repo.path} "
                    "(pass allow_nonempty=True to initialize anyway)"
                )
        else:
            try:
                repo.path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise RepositoryError(f"Cannot create repository at {repo.path}: {exc}") from exc
        with RepositoryLock(repo.lock_path, break_lock=break_lock):
            repo.object_store.init()
            repo.manifest_store.init()
            repo.tmp_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_json(repo.repo_json, default_repo_metadata())
        return repo

    def backup(
        self,
        source: Path,
        excludes: list[str] | None = None,
        dry_run: bool = False,
        strict: bool = False,
        break_lock: bool = False,
        skip_predicate: SkipPredicate = None,
    ) -> SnapshotResult:
        self._ensure_initialized()
        self._validate_backup_source(source)
        excludes, source_warnings = self._with_repo_self_exclude(source, excludes or [])
        excludes = [validate_exclude_pattern(pattern) for pattern in excludes]

        with RepositoryLock(self.lock_path, break_lock=break_lock) as lock:
            previous = self.manifest_store.latest()
            result = self.engine.build_snapshot(
                source,
                previous,
                excludes=excludes,
                dry_run=dry_run,
                strict=strict,
                skip_predicate=skip_predicate,
            )
            result.stale_lock_cleared_pid = lock.cleared_stale_pid
            result.warnings.extend(source_warnings)
            if dry_run or result.manifest is None:
                return result
            self._ensure_manifest_blobs_exist(result.manifest)
            self.manifest_store.save(result.manifest)
            result.committed = True
            return result

    def list_snapshots(self, break_lock: bool = False) -> list[SnapshotSummary]:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
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

    def repo_info(self, break_lock: bool = False) -> RepoInfo:
        self._ensure_repo_paths()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            metadata = json.loads(self.repo_json.read_text(encoding="utf-8"))
            manifests = self.manifest_store.list_manifests()
            last_backup_at = manifests[-1].created_at if manifests else None
            return RepoInfo(
                metadata=metadata,
                snapshot_count=len(manifests),
                object_count=len(self.object_store.iter_blob_paths()),
                last_backup_at=last_backup_at,
            )

    def show_snapshot(self, snapshot_id: str, break_lock: bool = False) -> Manifest:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            return self._resolve_snapshot(snapshot_id)

    def restore(
        self,
        snapshot_id: str,
        destination: Path,
        file_path: str | None = None,
        force: bool = False,
        safe_symlinks: bool = False,
        break_lock: bool = False,
    ) -> RestoreResult:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            manifest = self._resolve_snapshot(snapshot_id)
            return self.engine.restore_snapshot(
                manifest,
                destination,
                file_path=file_path,
                force=force,
                safe_symlinks=safe_symlinks,
            )

    def diff(self, snapshot_a: str, snapshot_b: str, break_lock: bool = False) -> DiffResult:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            a = self._resolve_snapshot(snapshot_a)
            b = self._resolve_snapshot(snapshot_b)
            return diff_manifests(a, b)

    def verify(self, snapshot_id: str, break_lock: bool = False) -> VerifyResult:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            try:
                manifest = self._resolve_snapshot(snapshot_id)
            except (ManifestError, RepositoryError) as exc:
                return VerifyResult(False, snapshot_id, [str(exc)])
            return verify_manifest(self, manifest)

    def check(self, break_lock: bool = False, repair: bool = False) -> CheckResult:
        self._ensure_repo_paths()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            return check_repository(self, repair=repair)

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
                    self.manifest_store.delete(manifest.snapshot_id)
                fsync_directory(self.snapshots_dir)

            gc_result = (
                self._gc_unlocked(dry_run=dry_run, manifests=kept)
                if run_gc
                else None
            )

            return PruneResult(
                deleted_snapshots=[manifest.snapshot_id for manifest in to_delete],
                kept_snapshots=[manifest.snapshot_id for manifest in kept],
                dry_run=dry_run,
                gc_result=gc_result,
            )

    def gc(self, dry_run: bool = False, aggressive: bool = False, break_lock: bool = False) -> GCResult:
        self._ensure_initialized()
        with RepositoryLock(self.lock_path, break_lock=break_lock):
            return self._gc_unlocked(dry_run=dry_run, aggressive=aggressive)

    def _gc_unlocked(
        self,
        dry_run: bool = False,
        manifests: list[Manifest] | None = None,
        aggressive: bool = False,
    ) -> GCResult:
        return gc_unlocked(
            self,
            dry_run=dry_run,
            manifests=manifests,
            aggressive=aggressive,
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
        self._ensure_repo_paths()
        try:
            metadata = json.loads(self.repo_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RepositoryError(f"Invalid repo.json: {exc}") from exc
        errors = validate_repo_metadata(metadata)
        if errors:
            raise RepositoryError("; ".join(errors))

    def _ensure_repo_paths(self) -> None:
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

    def _with_repo_self_exclude(self, source: Path, excludes: list[str]) -> tuple[list[str], list[str]]:
        """Auto-exclude the repository directory when it lives inside the source tree.

        If ``source`` is the repository itself, callers must reject the backup
        earlier via ``_validate_backup_source``. When the repository is a strict
        descendant of ``source`` (for example ``project/.mybackup`` while backing
        up ``project/``), append the relative repo path to ``excludes`` and
        return a warning so operators know the backup skipped repository data.
        """
        warnings: list[str] = []
        try:
            repo_relative = self.path.resolve().relative_to(source.resolve())
        except ValueError:
            return list(excludes), warnings

        if str(repo_relative) in ("", "."):
            raise RepositoryError("source must not be the repository directory")
        warnings.append(
            f"repository at {repo_relative.as_posix()} is inside the source; "
            "it was added to --exclude automatically"
        )
        return [*excludes, repo_relative.as_posix()], warnings

    def _validate_backup_source(self, source: Path) -> None:
        source_resolved = source.resolve()
        repo_resolved = self.path.resolve()
        if source_resolved == repo_resolved:
            raise RepositoryError("source must not be the repository directory")
        try:
            source_resolved.relative_to(repo_resolved)
        except ValueError:
            return
        raise RepositoryError("source must not be inside the repository directory")
