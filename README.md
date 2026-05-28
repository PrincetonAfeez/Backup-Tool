# Backup Tool

![Tests](https://github.com/PrincetonAfeez/Backup-Tool/actions/workflows/tests.yml/badge.svg)

A local Python backup tool that uses **content-addressable storage** and **immutable JSON
snapshot manifests** to create incremental, verifiable snapshots. The core package is
standard-library only; the CLI delegates to the `Repository` library API.

Licensed under [MIT](LICENSE).

## Features

- SHA-256 object store with whole-file and 1 MiB block chunk deduplication
- Immutable manifests with digest sidecars; `verify`, `check`, `diff`
- Staging-based backup and restore (no in-place manifest or destination mutation)
- Repository lock with stale-lock recovery; documented exit codes
- Retention (`prune`) and blob garbage collection (`gc`)

## Quick start

```powershell
python -m backup_tool init --repo .mybackup
python -m backup_tool backup . --repo .mybackup --exclude ".mybackup"
python -m backup_tool list --repo .mybackup
python -m backup_tool verify latest --repo .mybackup
python -m backup_tool restore latest --repo .mybackup --to restored
```

Equivalent forms: `python -m backup_tool.cli …` or the `backup-tool` console script after
`pip install -e .`.

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/README.md](docs/README.md) | Documentation hub |
| [docs/CASE_STUDY.md](docs/CASE_STUDY.md) | Portfolio-style design narrative |
| [docs/TDD.md](docs/TDD.md) | Technical design (flows, modules, types) |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Operations, health checks, recovery |
| [docs/LESSONS_LEARNED.md](docs/LESSONS_LEARNED.md) | Trade-offs and future work |
| [docs/IDS.md](docs/IDS.md) | Interface design (CLI contracts, I/O, side effects) |
| [docs/adr/README.md](docs/adr/README.md) | Architecture decision records (0001–0012) |
| [Schema/README.md](Schema/README.md) | JSON schemas aligned with runtime validation |
| [docs/RELEASE.md](docs/RELEASE.md) | Version bump checklist |

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

Mutating commands accept `--break-lock` when a crashed process left `lock` behind. Locks
whose PID is no longer running, empty locks, and malformed locks older than 24 hours are
cleared automatically; recent malformed non-empty locks still need `--break-lock`.

`list` marks the newest snapshot with `*` and partial snapshots with `[PARTIAL]`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error (invalid arguments, repository error) |
| 2 | Integrity or verification failure (`verify`, `check`) |
| 3 | Partial backup, strict abort, or restore symlink skips |
| 4 | Unexpected internal error |
| 5 | Could not acquire repository lock |

## Retention and disk usage

`prune` removes old snapshot manifests only. Unreferenced blobs remain until `gc`:

```powershell
backup-tool prune --repo .mybackup --keep 5 --gc
```

## Repository layout

```text
.mybackup/
    repo.json
    lock
    objects/<aa>/<full-sha256>
    snapshots/<snapshot-id>.json
    snapshots/<snapshot-id>.json.sha256
    tmp/staging/<snapshot-id>/...
```

## Example manifest

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
    "directory_count": 0,
    "entry_count": 2,
    "errors": 0,
    "new_bytes_stored": 12,
    "new_files": 1,
    "regular_file_count": 2,
    "skipped_files": 0,
    "symlink_count": 0,
    "unchanged_files": 1
  },
  "status": "complete",
  "version": 1
}
```

Large files (over 1 MiB) use fixed-size chunks ([ADR 0008](docs/adr/0008-fixed-size-block-chunking.md)).
`verify` loads the manifest digest sidecar and checks referenced blob content; it does not
detect malicious tampering when manifest and sidecar are rewritten together —
[ADR 0009](docs/adr/0009-manifest-trust-and-tamper-model.md).

JSON schemas under [`Schema/`](Schema/README.md) document on-disk formats; they ship with
the source repository only (not in the PyPI/wheel package).

## Safety rules

- Backup never mutates the source directory.
- Restore refuses destinations inside or containing the repository; writes via staging,
  then atomically replaces the destination (`--force` when overwrite is intended).
- **Symlinks:** default restore recreates symlink targets exactly (faithful archival).
  Use `--safe-symlinks` on untrusted repositories to reject absolute, drive-letter, UNC,
  empty, and `..`-escaping targets.
- Manifests are immutable after commit; snapshots commit only after referenced blobs exist.
- GC deletes only blobs unreferenced by all surviving snapshots.
- Partial snapshots warn on CLI; `--strict` aborts without commit.
- Repository inside the source tree is auto-excluded with a warning.
- **`--break-lock`:** use only after confirming no active `backup-tool` process holds the
  lock (backup, restore, check, prune, gc, migrate).

See [docs/RUNBOOK.md](docs/RUNBOOK.md) for failure modes and recovery.

## Exclude patterns

Patterns match manifest-relative POSIX paths. Patterns with `..` are rejected.

| Pattern | Matches |
|---------|---------|
| `*.tmp` | Basename at any depth |
| `__pycache__` | That directory and descendants |
| `build/` | Prefix `build/` (and the directory entry) |
| `tests/foo.py` | Exact path only (no basename fallback) |
| `/etc` | Same as `etc` (leading `/` is stripped) |
| `<repo>` inside source | Auto-excluded when the repository lives under the backup source (warning emitted) |

Patterns `*` and `**` are **rejected** at the CLI (they would exclude the entire tree).

`restore --file` merges selected paths into an existing `--to` directory without removing
unrelated files. A full restore (no `--file`) still atomically replaces `--to`.

## Development

```powershell
pip install -r requirements-dev.txt
pip install -e .
pytest
ruff check backup_tool tests
mypy backup_tool
```

CI: editable install, `backup-tool version` smoke test, pytest with 85% coverage floor,
ruff, mypy — Ubuntu and Windows, Python 3.11 and 3.12.

Windows symlink tests require Developer Mode (or equivalent); Ubuntu CI covers symlinks.

## Limitations

Intended for small local datasets: full tree in memory, no encryption/compression/remote
backend, no mtime fast path, stable-read may read each file up to three times. See
[docs/LESSONS_LEARNED.md](docs/LESSONS_LEARNED.md) and [ADR 0012](docs/adr/0012-scaling-and-incremental-scan-v2.md).

Legacy repos without digest sidecars: run `backup-tool migrate manifest-digests --repo <path>` once.
