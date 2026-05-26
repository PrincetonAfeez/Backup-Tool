# Backup Tool

A local Python backup tool that uses content-addressable storage and immutable
JSON manifests to create incremental, verifiable snapshots.

The core package is standard-library only. The CLI is intentionally thin and
calls the library API.

## Quick Start

```powershell
python -m backup_tool.cli init --repo .mybackup
python -m backup_tool.cli backup . --repo .mybackup --exclude ".mybackup"
python -m backup_tool.cli list --repo .mybackup
python -m backup_tool.cli verify latest --repo .mybackup
python -m backup_tool.cli restore latest --repo .mybackup --to restored
```

After installing the package, the same commands are available as `backup-tool`.

## Commands

```text
backup-tool version
backup-tool init --repo <path> [--break-lock]
backup-tool backup <src> --repo <path> [--exclude <pattern>] [--dry-run] [--strict] [--verbose] [--break-lock]
backup-tool list --repo <path>
backup-tool restore <snapshot> --repo <path> --to <destination> [--file <relative-path>] [--force] [--break-lock]
backup-tool diff <snapshot-a> <snapshot-b> --repo <path> [--verbose]
backup-tool verify <snapshot> --repo <path>
backup-tool check --repo <path>
backup-tool prune --repo <path> --keep N [--dry-run] [--gc] [--break-lock]
backup-tool gc --repo <path> [--dry-run] [--break-lock]
```

Mutating commands accept `--break-lock` to remove a lock file left behind by a
crashed process. Locks whose recorded PID is no longer running are cleared
automatically. Use `backup --verbose` to see when a stale lock was removed.

`list` marks the newest snapshot with `*` and highlights partial snapshots with
`[PARTIAL]`.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error (invalid arguments, repository error) |
| 2 | Integrity or verification failure (`verify`, `check`) |
| 3 | Backup completed with skipped files, or strict mode aborted |
| 4 | Unexpected internal error |
| 5 | Could not acquire repository lock |

## Retention and Disk Usage

`prune` removes old snapshot manifests only. Unreferenced blobs remain on disk
until garbage collection runs. Use `prune --gc` to prune manifests and reclaim
blob storage in one step, or run `gc` separately after pruning.

```powershell
backup-tool prune --repo .mybackup --keep 5 --gc
backup-tool prune --repo .mybackup --keep 5 --dry-run --gc
```

When `prune` deletes snapshots without `--gc`, the CLI prints a hint to run
`gc`. When `check` finds orphan blobs, it suggests the same.

## Example Manifest

Each snapshot is an immutable JSON file under `snapshots/`:

```json
{
  "created_at": "2026-05-26T13:00:00.123456Z",
  "files": {
    "notes/todo.txt": {
      "hash": "abc123…",
      "mtime": 1710000000.0,
      "size": 12,
      "type": "file"
    }
  },
  "hash_algorithm": "sha256",
  "snapshot_id": "2026-05-26T13-00-00-123456Z_abcd1234",
  "source": "C:\\Projects\\docs",
  "stats": {
    "changed_files": 0,
    "file_count": 1,
    "new_bytes_stored": 12,
    "new_files": 1,
    "skipped_files": 0
  },
  "status": "complete",
  "version": 1
}
```

See [docs/adr/README.md](docs/adr/README.md) for design decisions.

## Safety Rules

- Backup never mutates the source directory.
- Restore refuses to overwrite existing data unless `--force` is supplied.
- Snapshot manifests are immutable once committed.
- A snapshot is committed only after referenced blobs exist.
- Garbage collection deletes only blobs unreferenced by all surviving snapshots.
- Manifest paths are normalized relative paths and are checked during restore.
- Mutating repository operations use a lock file with stale-lock recovery.
- Partial snapshots (skipped files) emit CLI warnings; use `--strict` to abort instead.

## Repository Layout

```text
.mybackup/
    objects/
        ab/
            abcdef...
    snapshots/
        2026-05-26T13-00-00Z_abcd1234.json
    tmp/
    repo.json
    lock
```

Blob files are stored under `objects/<first-two-hash-chars>/<full-hash>`.

## Development

Run the test suite:

```powershell
python -m unittest discover -s tests -v
```

CI runs the same command on Ubuntu and Windows for Python 3.11 and 3.12.

On Windows, symlink tests are skipped unless Developer Mode (or equivalent
symlink privilege) is enabled. Ubuntu CI covers symlink backup/restore.

The implementation covers the Version 1 CLI/library core: init, backup, list,
restore, diff, verify, check, prune, gc, dry-run, excludes, strict mode,
repository locking with stale-lock recovery, path validation, and focused tests
for strict mode, partial snapshots, symlinks, partial restore, prune+gc, lock
behavior, and CLI polish.
