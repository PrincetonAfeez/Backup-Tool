"""Snapshot manifest models and storage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backup_tool.atomic import atomic_write_json, atomic_write_text
from backup_tool.errors import ManifestError, StoreError
from backup_tool.hashing import hash_file
from backup_tool.object_store import validate_hash
from backup_tool.paths import normalize_manifest_path


MANIFEST_VERSION = 1
MANIFEST_HASH_ALGORITHM = "sha256"
MANIFEST_STATUSES = frozenset({"complete", "partial", "dry-run"})
FILE_ENTRY_TYPES = frozenset({"file", "symlink", "directory"})


def manifest_digest_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(f"{manifest_path.name}.sha256")


def write_manifest_digest(manifest_path: Path) -> None:
    digest = hash_file(manifest_path).hash_hex
    atomic_write_text(manifest_digest_path(manifest_path), f"{digest}\n")


def verify_manifest_digest(manifest_path: Path) -> None:
    sidecar = manifest_digest_path(manifest_path)
    if not sidecar.exists():
        raise ManifestError(f"Manifest digest sidecar missing: {sidecar.name}")
    expected = sidecar.read_text(encoding="utf-8").strip()
    try:
        validate_hash(expected)
    except StoreError as exc:
        raise ManifestError(f"Invalid manifest digest sidecar: {exc}") from exc
    actual = hash_file(manifest_path).hash_hex
    if actual != expected:
        raise ManifestError(f"Manifest digest mismatch for {manifest_path.name}")


def _validate_content_hash(hash_hex: str, *, field: str = "hash") -> str:
    try:
        return validate_hash(hash_hex)
    except StoreError as exc:
        raise ManifestError(f"Invalid file entry {field}: {exc}") from exc


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

    def _validate(self) -> None:
        if self.type not in FILE_ENTRY_TYPES:
            raise ManifestError(f"Unsupported file entry type: {self.type}")
        if self.type == "file":
            if not self.hash:
                raise ManifestError("File entry is missing hash")
            _validate_content_hash(self.hash)
            if self.chunks is not None:
                if not self.chunks:
                    raise ManifestError("File entry chunks must be a non-empty list")
                for index, chunk_hash in enumerate(self.chunks):
                    _validate_content_hash(chunk_hash, field=f"chunks[{index}]")
        if self.type == "symlink" and self.target is None:
            raise ManifestError("Symlink entry is missing target")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileEntry":
        entry_type = data.get("type")
        if entry_type not in FILE_ENTRY_TYPES:
            raise ManifestError(f"Unsupported file entry type: {entry_type}")
        if entry_type == "file" and not data.get("hash"):
            raise ManifestError("File entry is missing hash")
        if entry_type == "symlink" and "target" not in data:
            raise ManifestError("Symlink entry is missing target")

        raw_chunks = data.get("chunks")
        chunks: tuple[str, ...] | None = None
        if raw_chunks is not None:
            if not isinstance(raw_chunks, list) or not raw_chunks:
                raise ManifestError("File entry chunks must be a non-empty list")
            chunks = tuple(_validate_content_hash(str(item), field=f"chunks[{index}]") for index, item in enumerate(raw_chunks))

        file_hash = data.get("hash")
        if file_hash is not None:
            file_hash = _validate_content_hash(str(file_hash))

        return cls(
            type=entry_type,
            hash=file_hash,
            size=data.get("size"),
            mtime=data.get("mtime"),
            mode=data.get("mode"),
            target=data.get("target"),
            chunks=chunks,
            is_dir_symlink=data.get("is_dir_symlink"),
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
        if self.type == "file":
            return (self.type, self.hash, self.chunks, self.mode)
        if self.type == "symlink":
            return (self.type, self.target, self.mode, self.is_dir_symlink)
        if self.type == "directory":
            return (self.type, self.mode)
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
        if self.hash_algorithm != MANIFEST_HASH_ALGORITHM:
            raise ManifestError(f"Unsupported manifest hash algorithm: {self.hash_algorithm}")
        if self.status not in MANIFEST_STATUSES:
            raise ManifestError(f"Unsupported manifest status: {self.status}")
        if not isinstance(self.stats, dict):
            raise ManifestError("Manifest stats must be an object")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        version = data.get("version")
        if version != MANIFEST_VERSION:
            raise ManifestError(f"Unsupported manifest version: {version}")

        files_data = data.get("files")
        if not isinstance(files_data, dict):
            raise ManifestError("Manifest files must be an object")

        required = ["snapshot_id", "created_at", "source", "hash_algorithm", "status", "stats"]
        missing = [key for key in required if key not in data]
        if missing:
            raise ManifestError(f"Manifest missing required keys: {', '.join(missing)}")

        hash_algorithm = str(data["hash_algorithm"])
        if hash_algorithm != MANIFEST_HASH_ALGORITHM:
            raise ManifestError(f"Unsupported manifest hash algorithm: {hash_algorithm}")

        status = str(data["status"])
        if status not in MANIFEST_STATUSES:
            raise ManifestError(f"Unsupported manifest status: {status}")

        stats_data = data["stats"]
        if not isinstance(stats_data, dict):
            raise ManifestError("Manifest stats must be an object")

        files: dict[str, FileEntry] = {}
        for raw_path, raw_entry in files_data.items():
            path = normalize_manifest_path(raw_path)
            if path in files:
                raise ManifestError(f"Duplicate normalized manifest path: {path}")
            if not isinstance(raw_entry, dict):
                raise ManifestError(f"Manifest entry must be an object: {path}")
            files[path] = FileEntry.from_dict(raw_entry)

        return cls(
            snapshot_id=str(data["snapshot_id"]),
            created_at=str(data["created_at"]),
            source=str(data["source"]),
            status=status,
            stats=dict(stats_data),
            files=files,
            skipped=list(data.get("skipped", [])),
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
        if "/" in snapshot_id or "\\" in snapshot_id or snapshot_id in ("", ".", ".."):
            raise ManifestError(f"Invalid snapshot id: {snapshot_id}")
        return self.snapshots_dir / f"{snapshot_id}.json"

    def save(self, manifest: Manifest) -> Path:
        path = self.path_for(manifest.snapshot_id)
        if path.exists():
            raise ManifestError(f"Snapshot already exists: {manifest.snapshot_id}")
        atomic_write_json(path, manifest.to_dict())
        write_manifest_digest(path)
        return path

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
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Could not load manifest {path}: {exc}") from exc
        return Manifest.from_dict(data)

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
