# Backup Tool

![Tests](https://github.com/PrincetonAfeez/Backup-Tool/actions/workflows/tests.yml/badge.svg)

A local Python backup tool that uses content-addressable storage and immutable
JSON snapshot manifests to create incremental, verifiable snapshots.

The core package is standard-library only. The CLI is intentionally thin and
calls the library API. Licensed under [MIT](LICENSE).

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
backup-tool init --repo <path> [--allow-nonempty] [--break-lock]
backup-tool backup <src> --repo <path> [--exclude <pattern>] [--dry-run] [--strict] [--verbose] [--break-lock]
backup-tool list --repo <path>
backup-tool info --repo <path>
backup-tool show <snapshot> --repo <path>
backup-tool restore <snapshot> --repo <path> --to <destination> [--file <relative-path>] [--force] [--safe-symlinks] [--break-lock]
backup-tool diff <snapshot-a> <snapshot-b> --repo <path> [--verbose]
backup-tool verify <snapshot> --repo <path>
backup-tool check --repo <path> [--repair] [--break-lock]
backup-tool prune --repo <path> --keep N [--dry-run] [--gc] [--break-lock]
backup-tool gc --repo <path> [--dry-run] [--aggressive] [--break-lock]
backup-tool migrate manifest-digests --repo <path> [--break-lock]
```

Mutating commands accept `--break-lock` to remove a lock file left behind by a
crashed process. Locks whose recorded PID is no longer running are cleared
automatically. Use `backup --verbose` to see when a stale lock was removed.

`list` marks the newest snapshot with `*` and highlights partial snapshots with
`[PARTIAL]`.

`info` prints repository metadata as JSON, then snapshot/object counts on stdout.
`show` prints the manifest as JSON on stdout (same keys as `Manifest.to_dict()`);
a one-line summary goes to stderr.
`diff` reports content and path topology changes between snapshots; metadata-only
changes (permissions, timestamps) are stored in manifests but not listed.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error (invalid arguments, repository error) |
| 2 | Integrity or verification failure (`verify`, `check`) |
| 3 | Operation completed partially: backup skipped files, strict backup aborted, or restore skipped symlinks |
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

## Block Chunking

Files larger than 1 MiB are split into fixed-size content-addressed chunks.
Smaller files remain stored as a single blob keyed by the file hash. Shared
chunks deduplicate across files and snapshots (see ADR 0008).

## Example Manifest

Each snapshot is a JSON file under `snapshots/`. Once committed, a manifest is
never modified in place; `prune` may delete old manifest files during retention.

```json
{
  "created_at": "2026-05-26T13:00:00.123456Z",
  "files": {
    "notes/todo.txt": {
      "hash": "abc123…",
      "mtime": 1710000000.0,
      "size": 12,
      "type": "file"
    },
    "empty-dir": {
      "mtime": 1710000000.0,
      "mode": 493,
      "type": "directory"
    },
    "archive/large.bin": {
      "chunks": ["chunk_hash_1…", "chunk_hash_2…"],
      "hash": "full_file_hash…",
      "size": 2097153,
      "type": "file"
    }
  },
  "hash_algorithm": "sha256",
  "snapshot_id": "2026-05-26T13-00-00-123456Z_abcd1234",
  "source": "C:\\Projects\\docs",
  "skipped": [],
  "stats": {
    "changed_files": 0,
    "directory_count": 1,
    "entry_count": 3,
    "new_bytes_stored": 12,
    "new_files": 1,
    "regular_file_count": 2,
    "skipped_files": 0,
    "symlink_count": 0
  },
  "status": "complete",
  "version": 1
}
```

See [docs/adr/README.md](docs/adr/README.md) for design decisions. [ADR 0009](docs/adr/0009-manifest-trust-and-tamper-model.md) explains what `verify` does and does not prove.

## Safety Rules

- Backup never mutates the source directory.
- Restore refuses destinations that are the repository, inside the repository, or
  a parent directory that contains the repository.
- Restore writes into a fresh staging directory first, then atomically replaces
  the destination. Without `--force`, restore refuses when the destination already
  exists (file) or is non-empty (directory). With `--force`, the destination may
  be replaced only after the staged restore completes successfully. If symlink
  restoration fails while the destination already exists, restore aborts before
  replacing it (exit code 1) instead of leaving a partial tree in place.
- Manifests are never modified after commit; `prune` deletes old manifest files
  during retention.
- `verify` detects missing blobs and hash mismatches (bit rot), not manifest
  tampering — see ADR 0009. Manifest digest sidecars catch accidental edits and
  partial writes, not an attacker who can change both files.
- A snapshot is committed only after referenced blobs exist.
- Garbage collection deletes only blobs unreferenced by all surviving snapshots.
- Manifest paths are normalized relative paths and are checked during restore.
- Mutating repository operations use a lock file with stale-lock recovery.
- Partial snapshots (skipped files) emit CLI warnings; use `--strict` to abort instead.
- When the repository directory lives inside the backup source tree, it is
  auto-excluded and a warning is printed (see `_with_repo_self_exclude` in code).

## Symlinks

By default, restore recreates symlink targets exactly as recorded in the manifest,
including absolute paths and `..` segments. Use `--safe-symlinks` on restore to
reject absolute or parent-escaping targets instead. Directory symlinks on Windows
use the `is_dir_symlink` flag recorded at backup time.

## Exclude Patterns

`--exclude` patterns match manifest-relative POSIX paths:

| Pattern | Matches |
|---------|---------|
| `*.tmp` | Any file whose basename is `*.tmp` at any depth |
| `__pycache__` | The `__pycache__` directory and everything under it |
| `build/` | Paths under the `build/` directory prefix |
| `tests/foo.py` | Only that exact path (patterns with `/` do not fall back to basename matching) |
| `dir/*.py` | Files directly under `dir/` whose names match `*.py` |

Patterns with `..` are rejected at the CLI with `Unsafe exclude pattern`.
A leading `/` is optional and stripped (for example `/etc` and `etc` both match
manifest paths under `etc/`). A trailing slash on a directory pattern (for
example `build/`) also excludes the directory entry itself, not only paths under
it.

**Basename quirk:** patterns without `/` (for example `*.tmp`) also match on
basename at any depth. Patterns with `/` (for example `tests/foo.py`) match only
the full manifest path and do not fall back to basename matching.

## Repository metadata

Each repository stores metadata in `repo.json`:

| Field | Meaning | Validated by `check` |
|-------|---------|----------------------|
| `version` | Repository format version (currently `1`) | yes |
| `created_at` | UTC timestamp when the repository was initialized | no |
| `hash_algorithm` | Content hash used for blobs (`sha256`) | yes |
| `storage` | Storage model (`content-addressable`) | yes |
| `object_layout` | Blob path layout (`sha256-prefix-2`) | yes |
| `chunking` | Large-file chunking scheme (`fixed-1mb-blocks-above-threshold`) | yes |

Use `backup-tool info --repo <path>` to print this metadata plus snapshot and
object counts. Use `backup-tool show <snapshot> --repo <path>` to inspect one
manifest without opening files manually.

Implementation modules: see [ADR 0010](docs/adr/0010-module-layout.md) for
`gc.py`, `verify.py`, and `metadata.py` (file mode/mtime restoration).

## Limitations

This tool is intended for small, local datasets in an academic setting:

- Backup walks the source tree and materializes the full file list in memory
  before sorting.
- No parallel backup, compression, or encryption.
- No incremental mtime-only fast path (every included file is re-hashed).
- Stable-file detection reads each accepted file up to three times: two hash
  passes to confirm unchanged content, then one store pass. The store pass is
  checked against the stable hash; if it diverges, the file is skipped. This is
  correct but expensive for large trees — see ADR 0012 for future work.
- Non-dry-run backups stage new blobs under `tmp/staging/<snapshot-id>/` during
  the scan and promote only blobs referenced by the committed manifest when the
  snapshot succeeds. Strict aborts discard staging entirely; partial snapshots do
  not promote blobs left over from skipped or failed file reads.
- `check` requires derived manifest stat keys (`entry_count`, file counts,
  `skipped_files`, `errors`, and similar) and cross-checks them against manifest
  contents. Diff- and scan-derived stats (`new_files`, `changed_files`,
  `new_bytes_stored`, and similar) are not validated.

Manifests committed before digest sidecars were added cannot be loaded until
you run `backup-tool migrate manifest-digests --repo <path>` once.

## Repository Layout

```text
.mybackup/
    objects/
        ab/
            abcdef...
    snapshots/
        2026-05-26T13-00-00Z_abcd1234.json
        2026-05-26T13-00-00Z_abcd1234.json.sha256
    tmp/
    repo.json
    lock
```

Blob files are stored under `objects/<first-two-hash-chars>/<full-hash>`.

## Development

Run the test suite with pytest:

```powershell
pip install -r requirements-dev.txt
pip install -e .
pytest
```

Run tests with coverage:

```powershell
coverage run -m pytest
coverage report -m
```

Run lint and type checks:

```powershell
ruff check backup_tool tests
mypy backup_tool
```

CI installs the package with `pip install -e .`, smoke-tests `backup-tool version`, runs pytest with coverage (85% minimum), ruff, and mypy on `backup_tool/` on Ubuntu and Windows for Python 3.11 and 3.12.

On Windows, symlink tests are skipped unless Developer Mode (or equivalent
symlink privilege) is enabled. Ubuntu CI covers symlink backup/restore.

The implementation covers the Version 1 CLI/library core: init, backup, list,
restore, diff, verify, check, prune, gc, dry-run, excludes, strict mode,
fixed-size block chunking for large files, repository locking with stale-lock
recovery, path validation, and focused tests for chunking, strict/partial
snapshots, symlinks, partial restore, prune+gc, lock behavior, CLI polish,
empty/unchanged backups, and concurrent lock contention.
