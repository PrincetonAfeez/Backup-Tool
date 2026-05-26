"""Snapshot manifest models and storage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backup_tool.atomic import atomic_write_json
from backup_tool.errors import ManifestError
from backup_tool.paths import normalize_manifest_path


MANIFEST_VERSION = 1


@dataclass(frozen=True)
class FileEntry:
    type: str
    hash: str | None = None
    size: int | None = None
    mtime: float | None = None
    mode: int | None = None
    target: str | None = None
    chunks: tuple[str, ...] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FileEntry":
        entry_type = data.get("type")
        if entry_type not in {"file", "symlink"}:
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
            chunks = tuple(str(item) for item in raw_chunks)

        return cls(
            type=entry_type,
            hash=data.get("hash"),
            size=data.get("size"),
            mtime=data.get("mtime"),
            mode=data.get("mode"),
            target=data.get("target"),
            chunks=chunks,
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
        return data

    def identity(self) -> tuple[Any, ...]:
        if self.type == "file":
            return (self.type, self.hash, self.chunks)
        if self.type == "symlink":
            return (self.type, self.target)
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
    hash_algorithm: str = "sha256"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Manifest":
        version = data.get("version")
        if version != MANIFEST_VERSION:
            raise ManifestError(f"Unsupported manifest version: {version}")

        files_data = data.get("files")
        if not isinstance(files_data, dict):
            raise ManifestError("Manifest files must be an object")

        files: dict[str, FileEntry] = {}
        for raw_path, raw_entry in files_data.items():
            path = normalize_manifest_path(raw_path)
            if path in files:
                raise ManifestError(f"Duplicate normalized manifest path: {path}")
            if not isinstance(raw_entry, dict):
                raise ManifestError(f"Manifest entry must be an object: {path}")
            files[path] = FileEntry.from_dict(raw_entry)

        required = ["snapshot_id", "created_at", "source", "hash_algorithm", "status", "stats"]
        missing = [key for key in required if key not in data]
        if missing:
            raise ManifestError(f"Manifest missing required keys: {', '.join(missing)}")

        return cls(
            snapshot_id=str(data["snapshot_id"]),
            created_at=str(data["created_at"]),
            source=str(data["source"]),
            status=str(data["status"]),
            stats=dict(data["stats"]),
            files=files,
            skipped=list(data.get("skipped", [])),
            version=version,
            hash_algorithm=str(data["hash_algorithm"]),
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
        return path

    def load(self, snapshot_id: str) -> Manifest:
        path = self.path_for(snapshot_id)
        if not path.exists():
            raise ManifestError(f"Snapshot not found: {snapshot_id}")
        return self.load_path(path)

    def load_path(self, path: Path) -> Manifest:
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
