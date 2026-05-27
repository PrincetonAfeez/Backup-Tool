"""Snapshot manifest models and storage."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backup_tool.atomic import atomic_write_text, fsync_directory
from backup_tool.errors import HashError, ManifestError, StoreError
from backup_tool.hashing import hash_file
from backup_tool.object_store import validate_hash
from backup_tool.paths import normalize_manifest_path
from backup_tool.staging import (
    validate_created_at,
    validate_manifest_stats,
    validate_skipped_items,
    validate_snapshot_id,
)


MANIFEST_VERSION = 1
MANIFEST_HASH_ALGORITHM = "sha256"


def validate_manifest_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestError("Manifest version must be an integer")
    if value != MANIFEST_VERSION:
        raise ManifestError(f"Unsupported manifest version: {value}")
    return value
MANIFEST_STATUSES = frozenset({"complete", "partial", "dry-run"})
FILE_ENTRY_TYPES = frozenset({"file", "symlink", "directory"})
DERIVED_MANIFEST_STAT_KEYS = frozenset(
    {
        "entry_count",
        "regular_file_count",
        "directory_count",
        "symlink_count",
        "skipped_files",
        "errors",
    }
)


def derived_manifest_stats(manifest: "Manifest") -> dict[str, int]:
    """Recompute manifest stats that are fully determined by manifest contents."""

    files = manifest.files
    skipped_count = len(manifest.skipped)
    return {
        "entry_count": len(files),
        "regular_file_count": sum(1 for entry in files.values() if entry.type == "file"),
        "directory_count": sum(1 for entry in files.values() if entry.type == "directory"),
        "symlink_count": sum(1 for entry in files.values() if entry.type == "symlink"),
        "skipped_files": skipped_count,
        "errors": skipped_count,
    }


def manifest_stats_consistency_errors(manifest: "Manifest") -> list[str]:
    """Return errors when stored stats disagree with derived counts.

    Diff- and scan-derived fields (``new_files``, ``changed_files``,
    ``deleted_files``, ``unchanged_files``, ``new_bytes_stored``,
    ``total_bytes_scanned``, ``new_blobs``) are not cross-checked.
    """

    derived = derived_manifest_stats(manifest)
    errors: list[str] = []
    missing = DERIVED_MANIFEST_STAT_KEYS - manifest.stats.keys()
    for key in sorted(missing):
        errors.append(f"{manifest.snapshot_id}: stats.{key} is missing")
    for key in DERIVED_MANIFEST_STAT_KEYS:
        if key in manifest.stats and manifest.stats[key] != derived[key]:
            errors.append(
                f"{manifest.snapshot_id}: stats.{key} is {manifest.stats[key]} "
                f"but manifest contains {derived[key]}"
            )
    return errors


MAX_FILE_MODE = 0o7777

IRRELEVANT_ENTRY_FIELDS: dict[str, frozenset[str]] = {
    "file": frozenset({"target", "is_dir_symlink"}),
    "symlink": frozenset({"hash", "size", "chunks"}),
    "directory": frozenset({"hash", "size", "chunks", "target", "is_dir_symlink"}),
}


@dataclass(frozen=True)
class MigrateDigestResult:
    migrated: list[str]
    skipped: list[str]


def manifest_digest_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f"{manifest_path.name}.sha256")


def write_manifest_digest(manifest_path: Path) -> None:
    digest = hash_file(manifest_path).hash_hex
    atomic_write_text(manifest_digest_path(manifest_path), f"{digest}\n")


def verify_manifest_digest(manifest_path: Path) -> None:
    sidecar = manifest_digest_path(manifest_path)
    if not sidecar.exists():
        raise ManifestError(f"Manifest digest sidecar missing: {sidecar.name}")
    try:
        expected = validate_hash(sidecar.read_text(encoding="utf-8").strip())
        actual = hash_file(manifest_path).hash_hex
    except (OSError, UnicodeDecodeError, HashError, StoreError) as exc:
        raise ManifestError(
            f"Could not verify manifest digest for {manifest_path.name}: {exc}"
        ) from exc
    if actual != expected:
        raise ManifestError(f"Manifest digest mismatch for {manifest_path.name}")


def _validate_content_hash(hash_hex: str, *, field: str = "hash") -> str:
    try:
        return validate_hash(hash_hex)
    except StoreError as exc:
        raise ManifestError(f"Invalid file entry {field}: {exc}") from exc


def _validate_optional_size(size: Any) -> int | None:
    if size is None:
        return None
    if isinstance(size, bool) or not isinstance(size, int):
        raise ManifestError("File entry size must be an integer")
    if size < 0:
        raise ManifestError("File entry size must be >= 0")
    return int(size)


def _validate_optional_mtime(mtime: Any) -> float | None:
    if mtime is None:
        return None
    if isinstance(mtime, bool) or not isinstance(mtime, (int, float)):
        raise ManifestError("File entry mtime must be numeric")
    value = float(mtime)
    if not math.isfinite(value):
        raise ManifestError("File entry mtime must be finite")
    return value


def _validate_optional_mode(mode: Any) -> int | None:
    if mode is None:
        return None
    if isinstance(mode, bool) or not isinstance(mode, int):
        raise ManifestError("File entry mode must be an integer")
    if mode < 0 or mode > MAX_FILE_MODE:
        raise ManifestError(f"File entry mode must be between 0 and {MAX_FILE_MODE}")
    return int(mode)


def _reject_irrelevant_entry_fields(entry_type: str, data: dict[str, Any]) -> None:
    for key in IRRELEVANT_ENTRY_FIELDS.get(entry_type, frozenset()):
        if key not in data:
            continue
        if data[key] is None:
            continue
        raise ManifestError(f"{entry_type} entry must not include {key}")


def _reject_irrelevant_entry_values(entry: "FileEntry") -> None:
    data = {
        "hash": entry.hash,
        "size": entry.size,
        "chunks": entry.chunks,
        "target": entry.target,
        "is_dir_symlink": entry.is_dir_symlink,
    }
    _reject_irrelevant_entry_fields(entry.type, data)


def _validate_manifest_source(source: Any) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ManifestError("Manifest source must be a non-empty string")
    return source


def _validate_manifest_string_field(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"Manifest {field} must be a string")
    return value


def _validate_manifest_topology(files: dict[str, FileEntry]) -> None:
    """Reject paths nested under a file or symlink entry."""

    for path in files:
        parent = path
        while True:
            slash = parent.rfind("/")
            if slash < 0:
                break
            parent = parent[:slash]
            ancestor = files.get(parent)
            if ancestor is not None and ancestor.type != "directory":
                raise ManifestError(
                    f"Manifest path conflicts with non-directory ancestor: "
                    f"{path} (ancestor {parent} is {ancestor.type})"
                )


def _normalize_manifest_files(files: Any) -> dict[str, FileEntry]:
    if not isinstance(files, dict):
        raise ManifestError("Manifest files must be an object")

    normalized_files: dict[str, FileEntry] = {}
    for raw_path, entry in files.items():
        if not isinstance(raw_path, (str, Path)):
            raise ManifestError("Manifest file path must be a string or Path")
        path = normalize_manifest_path(raw_path)
        if path in normalized_files:
            raise ManifestError(f"Duplicate normalized manifest path: {path}")
        if not isinstance(entry, FileEntry):
            raise ManifestError(f"Manifest entry must be a FileEntry: {path}")
        normalized_files[path] = entry
    _validate_manifest_topology(normalized_files)
    return normalized_files


def _validate_symlink_target(target: Any) -> str:
    if not isinstance(target, str):
        raise ManifestError("Symlink entry target must be a string")
    return target


def _validate_optional_is_dir_symlink(value: Any) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ManifestError("File entry is_dir_symlink must be a boolean")
    return value


@dataclass(frozen=True)
class FileEntry:
    type: str
    hash: str | None = None
    size: int | None = None
    mtime: float | None = None
    mode: int | None = None
    target: str | None = None
    chunks: tuple[str, ...] | None = None
    is_dir_symlink: bool | None = None

    def __post_init__(self) -> None:
        self._validate()
        self._normalize_hashes()

    def _normalize_hashes(self) -> None:
        if self.type != "file":
            return
        if self.hash:
            object.__setattr__(self, "hash", _validate_content_hash(self.hash))
        if self.chunks is not None:
            object.__setattr__(
                self,
                "chunks",
                tuple(
                    _validate_content_hash(chunk_hash, field=f"chunks[{index}]")
                    for index, chunk_hash in enumerate(self.chunks)
                ),
            )

    def _validate(self) -> None:
        if self.type not in FILE_ENTRY_TYPES:
            raise ManifestError(f"Unsupported file entry type: {self.type}")
        _reject_irrelevant_entry_values(self)
        if self.mtime is not None:
            _validate_optional_mtime(self.mtime)
        if self.type == "file":
            if not self.hash:
                raise ManifestError("File entry is missing hash")
            _validate_content_hash(self.hash)
            if self.size is not None:
                _validate_optional_size(self.size)
            if self.mode is not None:
                _validate_optional_mode(self.mode)
            if self.chunks is not None:
                if not self.chunks:
                    raise ManifestError("File entry chunks must be a non-empty list")
                for index, chunk_hash in enumerate(self.chunks):
                    _validate_content_hash(chunk_hash, field=f"chunks[{index}]")
        if self.type == "symlink":
            if self.target is None:
                raise ManifestError("Symlink entry is missing target")
            _validate_symlink_target(self.target)
            if self.mode is not None:
                _validate_optional_mode(self.mode)
            if self.is_dir_symlink is not None:
                _validate_optional_is_dir_symlink(self.is_dir_symlink)
        if self.type == "directory" and self.mode is not None:
            _validate_optional_mode(self.mode)

    @classmethod
    def from_dict(cls, data: object) -> "FileEntry":
        if not isinstance(data, dict):
            raise ManifestError("File entry must be an object")
        entry_type = data.get("type")
        if entry_type not in FILE_ENTRY_TYPES:
            raise ManifestError(f"Unsupported file entry type: {entry_type}")
        if entry_type == "file" and not data.get("hash"):
            raise ManifestError("File entry is missing hash")
        if entry_type == "symlink" and "target" not in data:
            raise ManifestError("Symlink entry is missing target")

        _reject_irrelevant_entry_fields(entry_type, data)

        raw_chunks = data.get("chunks")
        chunks: tuple[str, ...] | None = None
        if raw_chunks is not None:
            if not isinstance(raw_chunks, list) or not raw_chunks:
                raise ManifestError("File entry chunks must be a non-empty list")
            validated_chunks: list[str] = []
            for index, item in enumerate(raw_chunks):
                if not isinstance(item, str):
                    raise ManifestError(f"File entry chunks[{index}] must be a string")
                validated_chunks.append(
                    _validate_content_hash(item, field=f"chunks[{index}]")
                )
            chunks = tuple(validated_chunks)

        file_hash = data.get("hash")
        if file_hash is not None:
            if not isinstance(file_hash, str):
                raise ManifestError("File entry hash must be a string")
            file_hash = _validate_content_hash(file_hash)

        target = None
        if entry_type == "symlink":
            target = _validate_symlink_target(data["target"])

        return cls(
            type=entry_type,
            hash=file_hash,
            size=_validate_optional_size(data.get("size")),
            mtime=_validate_optional_mtime(data.get("mtime")),
            mode=_validate_optional_mode(data.get("mode")),
            target=target,
            chunks=chunks,
            is_dir_symlink=_validate_optional_is_dir_symlink(data.get("is_dir_symlink")),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type}
        if self.hash is not None:
            data["hash"] = self.hash
        if self.size is not None:
            data["size"] = self.size
        if self.mtime is not None:
            data["mtime"] = self.mtime
        if self.mode is not None:
            data["mode"] = self.mode
        if self.target is not None:
            data["target"] = self.target
        if self.chunks is not None:
            data["chunks"] = list(self.chunks)
        if self.is_dir_symlink is not None:
            data["is_dir_symlink"] = self.is_dir_symlink
        return data

    def identity(self) -> tuple[Any, ...]:
        """Content identity for diff classification (ADR 0002).

        Metadata such as mode and mtime are excluded so a touched file with
        unchanged bytes is not reported as changed.
        """
        if self.type == "file":
            return (self.type, self.hash, self.chunks, self.size)
        if self.type == "symlink":
            return (self.type, self.target)
        if self.type == "directory":
            return (self.type,)
        return (self.type,)


@dataclass
class Manifest:
    snapshot_id: str
    created_at: str
    source: str
    status: str
    stats: dict[str, int]
    files: dict[str, FileEntry]
    skipped: list[dict[str, str]] = field(default_factory=list)
    version: int = MANIFEST_VERSION
    hash_algorithm: str = MANIFEST_HASH_ALGORITHM

    def __post_init__(self) -> None:
        validate_manifest_version(self.version)
        validate_snapshot_id(self.snapshot_id)
        validate_created_at(self.created_at)
        object.__setattr__(self, "source", _validate_manifest_source(self.source))
        if self.hash_algorithm != MANIFEST_HASH_ALGORITHM:
            raise ManifestError(f"Unsupported manifest hash algorithm: {self.hash_algorithm}")
        if self.status not in MANIFEST_STATUSES:
            raise ManifestError(f"Unsupported manifest status: {self.status}")
        object.__setattr__(self, "stats", validate_manifest_stats(self.stats))
        object.__setattr__(self, "skipped", validate_skipped_items(self.skipped))
        object.__setattr__(self, "files", _normalize_manifest_files(self.files))

    @classmethod
    def from_dict(cls, data: object) -> "Manifest":
        if not isinstance(data, dict):
            raise ManifestError("Manifest root must be an object")

        version = validate_manifest_version(data.get("version"))

        files_data = data.get("files")
        if not isinstance(files_data, dict):
            raise ManifestError("Manifest files must be an object")

        required = ["snapshot_id", "created_at", "source", "hash_algorithm", "status", "stats"]
        missing = [key for key in required if key not in data]
        if missing:
            raise ManifestError(f"Manifest missing required keys: {', '.join(missing)}")

        hash_algorithm = _validate_manifest_string_field(
            data["hash_algorithm"],
            field="hash_algorithm",
        )
        if hash_algorithm != MANIFEST_HASH_ALGORITHM:
            raise ManifestError(f"Unsupported manifest hash algorithm: {hash_algorithm}")

        status = _validate_manifest_string_field(data["status"], field="status")
        if status not in MANIFEST_STATUSES:
            raise ManifestError(f"Unsupported manifest status: {status}")

        stats_data = data["stats"]
        stats = validate_manifest_stats(stats_data)
        skipped = validate_skipped_items(data.get("skipped", []))
        snapshot_id = validate_snapshot_id(data["snapshot_id"])
        created_at = validate_created_at(data["created_at"])
        source = _validate_manifest_source(data.get("source"))

        files: dict[str, FileEntry] = {}
        for raw_path, raw_entry in files_data.items():
            if not isinstance(raw_path, (str, Path)):
                raise ManifestError("Manifest file path must be a string or Path")
            path = normalize_manifest_path(raw_path)
            if path in files:
                raise ManifestError(f"Duplicate normalized manifest path: {path}")
            if not isinstance(raw_entry, dict):
                raise ManifestError(f"Manifest entry must be an object: {path}")
            files[path] = FileEntry.from_dict(raw_entry)

        return cls(
            snapshot_id=snapshot_id,
            created_at=created_at,
            source=source,
            status=status,
            stats=stats,
            files=files,
            skipped=skipped,
            version=version,
            hash_algorithm=hash_algorithm,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "source": self.source,
            "hash_algorithm": self.hash_algorithm,
            "status": self.status,
            "stats": self.stats,
            "files": {path: self.files[path].to_dict() for path in sorted(self.files)},
            "skipped": self.skipped,
        }


class ManifestStore:
    def __init__(self, snapshots_dir: Path):
        self.snapshots_dir = snapshots_dir

    def init(self) -> None:
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, snapshot_id: str) -> Path:
        validate_snapshot_id(snapshot_id)
        return self.snapshots_dir / f"{snapshot_id}.json"

    def save(self, manifest: Manifest) -> Path:
        path = self.path_for(manifest.snapshot_id)
        if path.exists():
            raise ManifestError(f"Snapshot already exists: {manifest.snapshot_id}")

        sidecar = manifest_digest_path(path)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        json_tmp: Path | None = None
        sidecar_tmp: Path | None = None
        fd_json: int | None = None
        fd_sidecar: int | None = None
        committed_sidecar = False
        committed_json = False

        try:
            fd_json, json_tmp_name = tempfile.mkstemp(
                prefix=f".{manifest.snapshot_id}.",
                suffix=".json.tmp",
                dir=self.snapshots_dir,
                text=True,
            )
            json_tmp = Path(json_tmp_name)
            try:
                fd_sidecar, sidecar_tmp_name = tempfile.mkstemp(
                    prefix=f".{manifest.snapshot_id}.",
                    suffix=".sha256.tmp",
                    dir=self.snapshots_dir,
                    text=True,
                )
                sidecar_tmp = Path(sidecar_tmp_name)
            except Exception:
                os.close(fd_json)
                fd_json = None
                json_tmp.unlink(missing_ok=True)
                json_tmp = None
                raise

            text = json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n"
            with os.fdopen(fd_json, "w", encoding="utf-8", newline="\n") as file:
                fd_json = None
                file.write(text)
                file.flush()
                os.fsync(file.fileno())

            digest = hash_file(json_tmp).hash_hex
            with os.fdopen(fd_sidecar, "w", encoding="utf-8", newline="\n") as file:
                fd_sidecar = None
                file.write(f"{digest}\n")
                file.flush()
                os.fsync(file.fileno())

            assert sidecar_tmp is not None
            os.replace(sidecar_tmp, sidecar)
            sidecar_tmp = None
            committed_sidecar = True
            assert json_tmp is not None
            os.replace(json_tmp, path)
            json_tmp = None
            committed_json = True
            fsync_directory(self.snapshots_dir)
            return path
        except Exception:
            if committed_json:
                path.unlink(missing_ok=True)
            if committed_sidecar:
                sidecar.unlink(missing_ok=True)
            raise
        finally:
            if fd_json is not None:
                os.close(fd_json)
            if fd_sidecar is not None:
                os.close(fd_sidecar)
            if json_tmp is not None:
                json_tmp.unlink(missing_ok=True)
            if sidecar_tmp is not None:
                sidecar_tmp.unlink(missing_ok=True)

    def delete(self, snapshot_id: str) -> None:
        path = self.path_for(snapshot_id)
        path.unlink(missing_ok=True)
        manifest_digest_path(path).unlink(missing_ok=True)

    def load(self, snapshot_id: str) -> Manifest:
        path = self.path_for(snapshot_id)
        if not path.exists():
            raise ManifestError(f"Snapshot not found: {snapshot_id}")
        return self.load_path(path)

    def load_path(self, path: Path) -> Manifest:
        verify_manifest_digest(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Could not load manifest {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ManifestError("Manifest root must be an object")
        manifest = Manifest.from_dict(data)
        if manifest.snapshot_id != path.stem:
            raise ManifestError(
                f"Manifest snapshot_id mismatch: file {path.stem}, "
                f"content {manifest.snapshot_id}"
            )
        return manifest

    def migrate_missing_digests(self) -> MigrateDigestResult:
        """Write missing digest sidecars for valid legacy manifests."""

        migrated: list[str] = []
        skipped: list[str] = []
        for path in self.list_paths():
            sidecar = manifest_digest_path(path)
            if sidecar.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                skipped.append(f"{path.name}: Could not load manifest: {exc}")
                continue
            if not isinstance(data, dict):
                skipped.append(f"{path.name}: Manifest root must be an object")
                continue
            try:
                manifest = Manifest.from_dict(data)
            except ManifestError as exc:
                skipped.append(f"{path.name}: {exc}")
                continue
            if manifest.snapshot_id != path.stem:
                skipped.append(
                    f"{path.name}: Manifest snapshot_id mismatch: file {path.stem}, "
                    f"content {manifest.snapshot_id}"
                )
                continue
            write_manifest_digest(path)
            migrated.append(path.stem)
        return MigrateDigestResult(migrated=migrated, skipped=skipped)

    def list_paths(self) -> list[Path]:
        if not self.snapshots_dir.exists():
            return []
        return sorted(self.snapshots_dir.glob("*.json"))

    def list_manifests(self) -> list[Manifest]:
        manifests = [self.load_path(path) for path in self.list_paths()]
        return sorted(manifests, key=lambda item: (item.created_at, item.snapshot_id))

    def latest(self) -> Manifest | None:
        manifests = self.list_manifests()
        return manifests[-1] if manifests else None
