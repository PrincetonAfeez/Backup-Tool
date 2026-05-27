"""Repository integrity verification."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from backup_tool.chunking import file_blob_hashes, verify_file_entry
from backup_tool.errors import IntegrityError, ManifestError, StoreError
from backup_tool.manifest import MANIFEST_VERSION, Manifest, manifest_stats_consistency_errors
from backup_tool.repo_metadata import validate_repo_metadata
from backup_tool.tmp_hygiene import (
    iter_orphan_staging_dirs,
    iter_stale_lock_tmp_files,
    iter_stale_manifest_tmp_files,
    remove_orphan_staging_dirs,
)

if TYPE_CHECKING:
    from backup_tool.repository import Repository


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
    quarantined_malformed: list[str] = field(default_factory=list)
    quarantined_manifests: list[str] = field(default_factory=list)
    repaired: bool = False


def _quarantine_manifest_path(repo: "Repository", path: Path) -> str:
    """Move an unloadable snapshot manifest into ``tmp/quarantine/``."""

    quarantine_dir = repo.tmp_dir / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    path_key = sha256(str(path).encode()).hexdigest()[:8]
    destination = quarantine_dir / f"{path.stem}__{path_key}{path.suffix}"
    shutil.move(str(path), str(destination))
    return str(destination)


def verify_manifest(repo: Repository, manifest: Manifest) -> VerifyResult:
    errors: list[str] = []
    warnings: list[str] = []

    if manifest.version != MANIFEST_VERSION:
        errors.append(f"Unsupported manifest version: {manifest.version}")

    for path, entry in manifest.files.items():
        if entry.type == "file":
            if not entry.hash:
                errors.append(f"{path}: file entry missing hash")
                continue
            try:
                verify_file_entry(repo.object_store, entry)
            except IntegrityError as exc:
                errors.append(f"{path}: {exc}")
            except (StoreError, OSError) as exc:
                errors.append(f"{path}: {exc}")
        elif entry.type == "symlink":
            if not entry.target:
                errors.append(f"{path}: symlink entry missing target")

    if manifest.status == "partial":
        warnings.append("snapshot is partial")

    return VerifyResult(not errors, manifest.snapshot_id, errors, warnings)


def check_repository(repo: Repository, *, repair: bool = False) -> CheckResult:
    errors: list[str] = []
    warnings: list[str] = []
    referenced: set[str] = set()
    snapshot_count = 0
    quarantined_malformed: list[str] = []
    quarantined_manifests: list[str] = []
    orphaned: set[str] = set()

    try:
        metadata = json.loads(repo.repo_json.read_text(encoding="utf-8"))
        errors.extend(validate_repo_metadata(metadata))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"Invalid repo.json: {exc}")

    for path in repo.manifest_store.list_paths():
        try:
            manifest = repo.manifest_store.load_path(path)
        except ManifestError as exc:
            if repair:
                moved_to = _quarantine_manifest_path(repo, path)
                quarantined_manifests.append(f"{path.name} -> {moved_to}")
                warnings.append(f"Quarantined unloadable manifest: {path.name}")
                continue
            errors.append(str(exc))
            continue
        snapshot_count += 1
        errors.extend(manifest_stats_consistency_errors(manifest))
        for manifest_path, entry in manifest.files.items():
            if entry.type == "file":
                if not entry.hash:
                    errors.append(f"{manifest.snapshot_id}:{manifest_path}: missing hash")
                    continue
                for blob_hash in file_blob_hashes(entry):
                    referenced.add(blob_hash)
                try:
                    verify_file_entry(repo.object_store, entry)
                except IntegrityError as exc:
                    errors.append(f"{manifest.snapshot_id}:{manifest_path}: {exc}")
                except (StoreError, OSError) as exc:
                    errors.append(f"{manifest.snapshot_id}:{manifest_path}: {exc}")

    malformed = repo.object_store.iter_malformed_paths()
    if malformed:
        if repair:
            quarantine_dir = repo.tmp_dir / "quarantine"
            moved = repo.object_store.quarantine_malformed(quarantine_dir, dry_run=False)
            quarantined_malformed = [
                f"{source} -> {hash_hex or 'unknown'}" for source, hash_hex in moved
            ]
        else:
            for path in malformed:
                hash_hex = repo.object_store.malformed_path_hash(path)
                detail = f"hash={hash_hex}" if hash_hex else "hash=unknown"
                errors.append(f"Malformed object path: {path} ({detail})")

    stale_tmp = repo.object_store.iter_stale_tmp_files()
    if stale_tmp:
        warnings.append(f"{len(stale_tmp)} stale blob tmp file(s) found")

    stale_manifest_tmp = iter_stale_manifest_tmp_files(repo.snapshots_dir)
    if stale_manifest_tmp:
        warnings.append(f"{len(stale_manifest_tmp)} stale manifest tmp file(s) found")

    stale_lock_tmp = iter_stale_lock_tmp_files(repo.path)
    if stale_lock_tmp:
        warnings.append(f"{len(stale_lock_tmp)} stale lock tmp file(s) found")

    orphan_digest_sidecars = sorted(
        sidecar
        for sidecar in repo.snapshots_dir.glob("*.json.sha256")
        if not sidecar.with_name(sidecar.name.removesuffix(".sha256")).exists()
    )
    removed_digest_sidecars = 0
    if orphan_digest_sidecars:
        if repair:
            for sidecar in orphan_digest_sidecars:
                sidecar.unlink(missing_ok=True)
                removed_digest_sidecars += 1
            if removed_digest_sidecars:
                warnings.append(
                    f"Removed {removed_digest_sidecars} orphan manifest digest sidecar(s)"
                )
        else:
            warnings.append(
                f"{len(orphan_digest_sidecars)} orphan manifest digest sidecar(s) found"
            )

    known_snapshot_ids = {
        path.stem
        for path in repo.manifest_store.list_paths()
        if path.suffix == ".json"
    }
    orphan_staging = iter_orphan_staging_dirs(
        repo.tmp_dir,
        known_snapshot_ids=known_snapshot_ids,
    )
    if orphan_staging:
        if repair:
            removed_staging, staging_bytes = remove_orphan_staging_dirs(
                repo.tmp_dir,
                known_snapshot_ids=known_snapshot_ids,
                dry_run=False,
            )
            if removed_staging:
                warnings.append(
                    f"Removed {len(removed_staging)} orphan staging "
                    f"director{'y' if len(removed_staging) == 1 else 'ies'}; "
                    f"bytes={staging_bytes}"
                )
        else:
            warnings.append(
                f"{len(orphan_staging)} orphan staging "
                f"director{'y' if len(orphan_staging) == 1 else 'ies'} found"
            )

    all_hashes = {hash_hex for hash_hex, _path in repo.object_store.iter_blob_paths()}
    orphaned = all_hashes - referenced
    if orphaned:
        warnings.append(f"{len(orphaned)} orphan blob(s) found")

    return CheckResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        snapshot_count=snapshot_count,
        object_count=len(all_hashes),
        referenced_object_count=len(referenced),
        orphan_object_count=len(orphaned),
        quarantined_malformed=quarantined_malformed,
        quarantined_manifests=quarantined_manifests,
        repaired=bool(
            quarantined_malformed
            or quarantined_manifests
            or (repair and orphan_staging)
            or removed_digest_sidecars
        ),
    )
