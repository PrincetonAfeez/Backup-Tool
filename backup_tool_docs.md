# Architecture Decision Record
## App — Backup Tool
**Local Resilience Group | Document 1 of 5**
**Status: Accepted**

---

## Context

The Local Resilience group requires a command-line backup tool that demonstrates practical file-system architecture without depending on cloud services, databases, GUI frameworks, or non-standard runtime packages. The tool must create repeatable local backups, avoid storing duplicate content, allow restores, verify integrity, support pruning and garbage collection, and expose enough operational commands to inspect and maintain a repository over time.

The project is intentionally packaged as both a library and CLI. The `backup-tool` console script and `python -m backup_tool.cli` entry point call the same repository API. The core package is standard-library only, requires Python 3.11 or newer, and stores data in a local repository directory containing content-addressed blobs, immutable snapshot manifests, digest sidecars, temporary staging space, repository metadata, and a lock file.

The decision was to build a content-addressable local backup system with immutable JSON snapshot manifests, SHA-256 object storage, fixed-size chunking for large files, explicit repository locking, verification commands, and cautious restore behavior.

---

## Decisions

### Decision 1 — Standard-library core over service dependency

**Chosen:** Implement the backup engine with Python standard-library modules only.

**Rejected:** SQLite, cloud storage SDKs, compression libraries, encryption packages, filesystem watcher libraries, or backup-specific external dependencies.

**Reason:** The educational goal is to demonstrate file traversal, hashing, manifest design, atomic writes, locks, restore safety, and command-line interfaces directly. Avoiding runtime dependencies makes the behavior easier to inspect and keeps installation simple.

---

### Decision 2 — Library API first, CLI second

**Chosen:** `Repository` is the user-facing library API. The CLI is intentionally thin and delegates to `Repository` methods.

**Rejected:** A CLI-only script where all behavior lives inside argparse branches.

**Reason:** Backup logic needs direct tests without subprocess overhead. A library API also makes the design clearer: repository initialization, backup, restore, diff, verify, check, prune, garbage collection, metadata, and migration are domain operations, not just CLI branches.

---

### Decision 3 — Content-addressable storage

**Chosen:** Store file bytes under `objects/<first-two-hash-chars>/<full-sha256>`.

**Rejected:** Copying full directory trees into each snapshot.

**Reason:** Content-addressed objects deduplicate unchanged files across snapshots. If two files or two snapshots contain identical bytes, they reference the same object. This is more efficient and makes verification straightforward because object names are their SHA-256 hashes.

---

### Decision 4 — Immutable JSON snapshot manifests

**Chosen:** Each snapshot is a JSON manifest under `snapshots/`, and committed manifests are never modified in place.

**Rejected:** Mutable snapshot database rows or rewriting a latest manifest.

**Reason:** Immutable manifests give a durable audit trail. Retention becomes explicit deletion of old manifest files, not mutation. JSON is inspectable, portable, and easy to test. The trade-off is that repository consistency depends on validating and loading many files rather than querying a database.

---

### Decision 5 — Manifest digest sidecars

**Chosen:** Every committed manifest has a `.sha256` sidecar. Legacy manifests without sidecars require a one-time `migrate manifest-digests` command.

**Rejected:** Trusting JSON manifests without a separate digest.

**Reason:** Digest sidecars catch accidental edits, truncation, and partial writes. They do not protect against a malicious actor who can modify both the manifest and the sidecar, but they provide useful local integrity checks within the project's threat model.

---

### Decision 6 — Fixed-size chunking for large files

**Chosen:** Files larger than 1 MiB are split into fixed-size content-addressed chunks. Smaller files are stored as one blob.

**Rejected:** Whole-file-only storage for every file.

**Reason:** Large files often change in parts. Chunking allows shared chunks to deduplicate across file versions. Fixed-size chunks are simpler than content-defined chunking and are sufficient for this academic version.

---

### Decision 7 — Stable-read detection over mtime-only fast path

**Chosen:** During backup, accepted files are hashed twice to confirm stable content, then read again for storage. The store pass is checked against the stable hash.

**Rejected:** Relying only on mtime/size or hashing each file once.

**Reason:** Backup should avoid committing files that changed while being read. This approach is slower but safer. It is an explicit cost accepted for correctness in a small local backup tool.

---

### Decision 8 — Staging before object promotion

**Chosen:** Non-dry-run backups stage blobs under `tmp/staging/<snapshot-id>/`, then promote them into `objects/` only when the snapshot succeeds.

**Rejected:** Writing directly into the final object store during a scan.

**Reason:** Staging reduces orphan blobs after strict aborts or failed scans. The snapshot is only committed after all referenced blobs exist. If the scan fails or aborts, staging is discarded.

---

### Decision 9 — Repository lock file with stale-lock recovery

**Chosen:** Mutating repository operations acquire an exclusive lock file that records process ID, timestamp, and token. Stale locks whose PID is no longer alive can be cleared automatically; `--break-lock` is available for operator recovery.

**Rejected:** No locking, OS-specific file locks only, or blocking indefinitely.

**Reason:** Backup, restore, prune, garbage collection, check repair, and migrations must not race against each other. The lock file is simple, portable, and inspectable. The token prevents one process from deleting another process's active lock on release.

---

### Decision 10 — Restore into staging before replacement

**Chosen:** Restore writes into a fresh temporary staging directory first. Only after all selected entries are restored does the tool replace the destination. Without `--force`, restore refuses to overwrite an existing file or non-empty directory.

**Rejected:** Writing directly into the destination tree.

**Reason:** Partial restore failures should not corrupt an existing destination. The staging-first approach gives a safer all-or-nothing restore path for normal file and directory entries.

---

### Decision 11 — Symlink preservation with optional safe mode

**Chosen:** By default, symlinks are restored exactly as recorded. `--safe-symlinks` rejects absolute or parent-escaping symlink targets.

**Rejected:** Always dereferencing symlinks or always rejecting risky targets.

**Reason:** Some backups need exact symlink preservation. Other restore contexts require stricter safety. Making this a restore-time option keeps both behaviors explicit.

---

### Decision 12 — Verification checks objects, not full tamper resistance

**Chosen:** `verify` checks missing blobs, blob hash mismatches, chunk integrity, and partial snapshot warnings. `check` validates repository metadata, manifests, object references, malformed object paths, stale temp files, and orphan blobs.

**Rejected:** Claiming cryptographic authenticity of the entire backup repository.

**Reason:** The tool can detect bit rot and accidental corruption. It cannot prove a manifest was not maliciously rewritten together with its sidecar. The documentation calls this out honestly.

---

## Consequences

**Positive:**
- Backup repositories are inspectable on disk.
- Unchanged content deduplicates across snapshots.
- Large files can reuse unchanged chunks.
- Immutable manifests provide clear snapshot history.
- Digest sidecars catch accidental manifest corruption.
- Restore is cautious and staging-based.
- Verification and check commands give operational confidence.
- Locks prevent most concurrent mutation races.
- The CLI is easy to script and package.
- The standard-library core keeps runtime installation minimal.

**Negative / Trade-offs:**
- No encryption, compression, or remote backend.
- Every accepted file is re-hashed; there is no mtime-only fast path.
- Backup materializes and sorts the full file list in memory.
- Stable-file detection can read accepted files up to three times.
- Fixed-size chunking is simpler but less efficient than content-defined chunking.
- Digest sidecars do not stop an attacker who can rewrite both manifest and sidecar.
- Symlink restore can be risky unless `--safe-symlinks` is used.
- Repository maintenance requires understanding prune versus garbage collection.

---

## Alternatives Not Explored

- SQLite manifest database.
- Restic/Borg-style encryption and compression.
- Content-defined chunking.
- Remote object storage.
- Parallel backup workers.
- Incremental mtime-only scan cache.
- Full Windows ACL or POSIX extended-attribute backup.
- Authenticated manifest signatures.

---

*Constitution reference: Article 1 (Python fundamentals and architecture), Article 3.4 (larger project classification), Article 4 (quality proportional to scope), Article 6 (verification beyond running locally), and Article 7 (progressive complexity).* 

---


# Technical Design Document
## App — Backup Tool
**Local Resilience Group | Document 2 of 5**

---

## Overview

Backup Tool is a local Python package and CLI for creating incremental, verifiable filesystem snapshots. It stores file content in SHA-256 content-addressed objects and records snapshot metadata in immutable JSON manifests. The CLI exposes repository initialization, backup, listing, info, show, restore, diff, verify, check, prune, garbage collection, and migration commands.

**Package name:** `backup_tool`  
**Console script:** `backup-tool`  
**Python entry point:** `python -m backup_tool.cli`  
**Runtime requirement:** Python 3.11+  
**Runtime dependencies:** none  
**Hash algorithm:** SHA-256  
**Repository version:** 1  
**Storage model:** content-addressable  
**Large-file chunking:** fixed 1 MiB blocks for files larger than 1 MiB

---

## Data Flow

### Repository initialization

```text
backup-tool init --repo .mybackup
     │
     ▼
Repository.init(path)
     │
     ├── reject existing repo.json
     ├── reject non-empty directory unless --allow-nonempty
     ├── acquire RepositoryLock
     ├── create objects/, snapshots/, tmp/
     └── write repo.json atomically
```

---

### Backup flow

```text
backup-tool backup <src> --repo <repo>
     │
     ▼
Repository.backup()
     │
     ├── validate repo metadata
     ├── reject source == repo or source inside repo
     ├── auto-exclude repo if repo is inside source
     ├── validate exclude patterns
     ├── acquire RepositoryLock
     ├── load previous latest manifest
     └── SnapshotEngine.build_snapshot()
             │
             ├── begin object staging
             ├── walk source tree without following directory symlinks
             ├── record directories
             ├── record symlinks
             ├── stable-read regular files
             ├── store small files as single blobs
             ├── store large files as fixed chunks
             ├── classify added/changed/deleted/unchanged entries
             ├── produce Manifest
             └── promote staging if successful
     │
     ├── ensure manifest references existing blobs
     ├── save manifest and digest sidecar
     └── release lock
```

---

### Restore flow

```text
backup-tool restore <snapshot> --repo <repo> --to <dest>
     │
     ▼
Repository.restore()
     │
     ├── acquire lock
     ├── resolve snapshot id or latest
     └── SnapshotEngine.restore_snapshot()
             │
             ├── select whole snapshot or --file subtree
             ├── reject unsafe destination unless --force
             ├── create staging restore directory
             ├── restore files from blobs/chunks
             ├── recreate directories
             ├── recreate symlinks or reject unsafe symlinks
             ├── restore mtime/mode metadata where possible
             └── atomically replace destination
```

---

### Verification flow

```text
backup-tool verify latest --repo .mybackup
     │
     ▼
Repository.verify()
     │
     ├── resolve manifest
     └── verify_manifest()
             │
             ├── validate manifest version
             ├── for each file entry:
             │     ├── verify whole blob or chunks exist
             │     ├── verify blob hash
             │     ├── verify chunk hash
             │     ├── verify composite file hash
             │     └── verify size when present
             └── warn if snapshot is partial
```

---

### Retention and garbage collection flow

```text
backup-tool prune --repo .mybackup --keep 5 --gc
     │
     ▼
Repository.prune()
     │
     ├── acquire lock
     ├── delete old manifest + sidecar files
     ├── optionally call gc_unlocked()
     └── report deleted snapshots/blobs
```

---

## Module-Level Structure

```text
backup_tool/
  __init__.py
  atomic.py
  chunking.py
  cli.py
  diff.py
  errors.py
  gc.py
  hashing.py
  lock.py
  manifest.py
  metadata.py
  object_store.py
  paths.py
  repo_metadata.py
  repository.py
  snapshot_engine.py
  staging.py
  tmp_hygiene.py
  verify.py

pyproject.toml
requirements-dev.txt
README.md
.github/workflows/tests.yml
docs/adr/
tests/
```

---

## Dependency Graph

```text
cli.py
  ├── argparse/json/sys/pathlib
  ├── backup_tool.__version__
  ├── errors
  ├── paths.validate_exclude_pattern
  └── repository.Repository

repository.py
  ├── atomic
  ├── chunking.file_blob_hashes
  ├── diff
  ├── gc
  ├── lock.RepositoryLock
  ├── manifest.ManifestStore
  ├── object_store.ObjectStore
  ├── paths
  ├── repo_metadata
  ├── snapshot_engine.SnapshotEngine
  └── verify

snapshot_engine.py
  ├── chunking.hash_file_content/store_file/restore_file_content
  ├── diff.classify_entries
  ├── manifest.FileEntry/Manifest
  ├── metadata.restore_entry_metadata
  ├── object_store.ObjectStore
  ├── paths safety helpers
  └── staging snapshot id helpers

manifest.py
  ├── atomic text writes
  ├── hashing.hash_file
  ├── object_store.validate_hash
  ├── paths.normalize_manifest_path
  └── staging validators

object_store.py
  ├── atomic fsync_directory
  ├── hashing.hash_file
  ├── staging validators
  └── tempfile/os/shutil

verify.py
  ├── chunking.file_blob_hashes/verify_file_entry
  ├── manifest.Manifest
  ├── repo_metadata.validate_repo_metadata
  └── tmp_hygiene
```

---

## Core Data Structures

### Repository layout

```text
.mybackup/
  repo.json
  lock
  objects/
    ab/
      abcdef...
  snapshots/
    <snapshot-id>.json
    <snapshot-id>.json.sha256
  tmp/
    staging/
    quarantine/
```

---

### `Repository`

Public library facade for one repository on disk.

Important paths:
- `path`
- `objects_dir`
- `snapshots_dir`
- `tmp_dir`
- `repo_json`
- `lock_path`

Important collaborators:
- `ObjectStore`
- `ManifestStore`
- `SnapshotEngine`

Important methods:
- `init`
- `backup`
- `list_snapshots`
- `repo_info`
- `show_snapshot`
- `restore`
- `diff`
- `verify`
- `check`
- `prune`
- `gc`
- `migrate_manifest_digests`

---

### `FileEntry`

One manifest entry.

Fields:
- `type`: `file`, `symlink`, or `directory`
- `hash`: full file SHA-256 for file entries
- `size`
- `mtime`
- `mode`
- `target`: symlink target
- `chunks`: chunk SHA-256 values for large files
- `is_dir_symlink`: Windows directory symlink hint

Validation rules:
- file entries require `hash`
- symlink entries require `target`
- directory entries cannot include file-only fields
- chunk hashes must be valid SHA-256 strings
- modes must be in a safe numeric range

---

### `Manifest`

One immutable snapshot.

Fields:
- `version`
- `snapshot_id`
- `created_at`
- `source`
- `hash_algorithm`
- `status`
- `stats`
- `files`
- `skipped`

Statuses:
- `complete`
- `partial`
- `dry-run`

---

### `SnapshotResult`

Result from backup build.

Fields:
- `manifest`
- `diff`
- `committed`
- `dry_run`
- `skipped`
- `errors`
- `warnings`
- `stale_lock_cleared_pid`

---

### `RestoreResult`

Result from restore.

Fields:
- `snapshot_id`
- `destination`
- `restored_files`
- `restored_symlinks`
- `restored_directories`
- `failed_symlinks`
- `warnings`

Status is `partial` when symlink restore failures occur.

---

### `BlobInfo` and `StoredFileInfo`

`BlobInfo` records one object-store write. `StoredFileInfo` records whole-file hash, size, optional chunk list, number of new blobs, and bytes stored.

---

### `VerifyResult` and `CheckResult`

`VerifyResult` reports one snapshot's verification. `CheckResult` reports whole-repository integrity, object counts, orphan counts, warnings, errors, malformed object quarantine, and repair state.

---

### `GCResult`

Garbage collection result.

Fields:
- `deleted_blobs`
- `kept_blobs`
- `dry_run`
- `bytes_deleted`
- `quarantined_malformed`
- `removed_tmp_files`
- `tmp_bytes_deleted`
- `aggressive`

---

## Function and Class Reference

### `Repository.init(path, break_lock=False, allow_nonempty=False)`

Initializes repository directories and `repo.json`. Refuses to overwrite an existing repository. Refuses non-empty directories unless explicitly allowed.

---

### `Repository.backup(source, excludes=None, dry_run=False, strict=False, break_lock=False, skip_predicate=None)`

Creates a snapshot result. It validates repository metadata, prevents backing up the repository itself, auto-excludes a repository directory inside the source, acquires a lock, builds a snapshot, verifies referenced blobs exist, then saves the manifest.

---

### `SnapshotEngine.build_snapshot()`

Walks source files, records directories and symlinks, stable-reads regular files, writes staged blobs, calculates diff stats, and returns a manifest result.

Important behavior:
- `os.walk(..., followlinks=False)`
- manifest paths are normalized POSIX-style relative paths
- skipped files become partial snapshots unless `--strict` aborts
- dry runs compute without committing
- staged objects are discarded after use

---

### `SnapshotEngine.restore_snapshot()`

Restores a full snapshot or selected subtree. It validates the destination, stages restored content, verifies chunk/blob content while restoring, restores metadata where possible, recreates symlinks, and atomically replaces the target.

---

### `ManifestStore.save(manifest)`

Writes manifest JSON and digest sidecar via temporary files and atomic replacement. If either commit step fails, it cleans up committed pieces.

---

### `ManifestStore.load(snapshot_id)`

Loads and validates manifest JSON after checking the `.sha256` sidecar.

---

### `ObjectStore.put_file(path)`

Streams a source file into a temporary blob while computing SHA-256. If a valid blob already exists, the temp copy is discarded and no new bytes are counted.

---

### `ObjectStore.put_bytes(data)`

Stores one in-memory chunk by SHA-256.

---

### `ObjectStore.promote_staging(snapshot_id)`

Moves staged blobs into the final object layout and skips blobs already present and valid.

---

### `hash_file_content(path)`

Hashes a file without storing it. Large files return a full-file hash plus chunk hashes.

---

### `store_file(store, path, dry_run=False)`

Stores a file as one blob or as fixed-size chunks, depending on size.

---

### `verify_file_entry(store, entry)`

Verifies missing blobs, chunk hashes, composite file hash, and sizes.

---

### `restore_file_content(store, entry, target)`

Streams stored blob or chunks into the restore target and re-checks restored content.

---

### `RepositoryLock`

Creates an exclusive lock with process ID, time, and token. It can clear stale locks and avoids deleting a lock that does not match its token.

---

### `safe_restore_path(destination, manifest_path)`

Ensures a manifest path cannot escape the staging restore root.

---

### `assert_safe_symlink_target(target, manifest_path=...)`

Rejects empty, absolute, drive-letter, UNC, or `..` symlink targets in safe symlink mode.

---

### `verify_manifest(repo, manifest)`

Verifies file entries and reports partial snapshot warnings.

---

### `check_repository(repo, repair=False)`

Validates repository metadata, manifest sidecars, blob references, object integrity, malformed object paths, stale temporary files, and orphan blobs.

---

### `gc_unlocked(repo, dry_run=False, manifests=None, aggressive=False)`

Deletes blobs not referenced by surviving manifests. In aggressive mode, malformed object paths and stale tmp files are handled too.

---

## Error Handling Strategy

- CLI returns documented exit codes instead of tracebacks for expected failures.
- `IntegrityError` maps to exit code 2.
- `LockError` maps to exit code 5.
- strict backup with skipped files exits 3 and commits no snapshot.
- partial backup with skipped files exits 3 after committing a partial snapshot.
- invalid arguments and repository errors map to exit code 1.
- unexpected exceptions map to exit code 4.
- restore refuses unsafe destinations unless `--force` is provided.
- check can quarantine malformed object paths when `--repair` is used.

---

## External Dependencies

Runtime dependencies: none.

Development dependencies include:
- pytest
- coverage
- ruff
- mypy

Packaging uses setuptools through `pyproject.toml`.

---

## Concurrency Model

The tool is synchronous and single-process per repository mutation. It does not use threads, multiprocessing, async I/O, or background jobs. Repository mutation operations use a lock file to prevent concurrent writes.

Lock-protected operations include:
- init
- backup
- list/show/info
- restore
- diff
- verify
- check
- prune
- gc
- migrate manifest digests

---

## Known Limits

- Intended for small local datasets.
- No encryption.
- No compression.
- No remote repository backend.
- No parallel scanning.
- No mtime-only fast path.
- Full file list is materialized and sorted in memory.
- Accepted files may be read up to three times.
- Fixed-size chunking is simple but not as dedupe-efficient as content-defined chunking.
- Manifest digest sidecars are not a defense against a writer who can alter both manifest and digest.
- Symlink tests depend on OS privileges on Windows.

---

## Verification Summary

The project documents and configures:
- pytest test discovery under `tests/`
- coverage on `backup_tool` with 85% fail-under
- ruff linting
- mypy type checks for `backup_tool`
- GitHub Actions on Ubuntu and Windows
- Python 3.11 and 3.12 CI matrix
- console script smoke test through `backup-tool version`

The README describes coverage for init, backup, list, restore, diff, verify, check, prune, gc, dry-run, excludes, strict mode, fixed-size chunking, locking, path validation, symlinks, partial restore, empty and unchanged backups, CLI polish, and concurrent lock contention.

---

*Constitution reference: Article 4 (engineering quality), Article 6 (behavior verification), Article 7 (progressive complexity), and Article 8 (valid learner work).* 

---


# Interface Design Specification
## App — Backup Tool
**Local Resilience Group | Document 3 of 5**

---

## Public CLI Interface

Primary command:

```text
backup-tool <command> [options]
```

Equivalent module invocation:

```text
python -m backup_tool.cli <command> [options]
```

---

## Command Reference

| Command | Purpose |
|---|---|
| `version` | Print package version |
| `init` | Initialize repository |
| `backup` | Create snapshot |
| `list` | List snapshots |
| `info` | Show repo metadata and counts |
| `show` | Print snapshot manifest JSON |
| `restore` | Restore snapshot or one file/subtree |
| `diff` | Compare two snapshots |
| `verify` | Verify one snapshot |
| `check` | Check full repository |
| `prune` | Delete old snapshot manifests |
| `gc` | Delete unreferenced blobs |
| `migrate manifest-digests` | Write missing digest sidecars |

---

## Invocation Syntax

### Version

```powershell
backup-tool version
```

Output:
```text
<version>
```

---

### Init

```powershell
backup-tool init --repo <path> [--allow-nonempty] [--break-lock]
```

| Argument | Required | Description |
|---|---:|---|
| `--repo <path>` | Yes | Repository directory |
| `--allow-nonempty` | No | Allow initializing inside an existing non-empty directory |
| `--break-lock` | No | Force-remove a stale/crashed lock |

Success output:
```text
Initialized repository: <path>
```

---

### Backup

```powershell
backup-tool backup <src> --repo <path> [--exclude <pattern>] [--dry-run] [--strict] [--verbose] [--break-lock]
```

| Argument | Required | Description |
|---|---:|---|
| `<src>` | Yes | Source directory to snapshot |
| `--repo <path>` | Yes | Backup repository |
| `--exclude <pattern>` | No | Pattern to exclude; repeatable |
| `--dry-run` | No | Build manifest/diff without committing |
| `--strict` | No | Abort if any file is skipped |
| `--verbose` | No | Show diff and skipped details |
| `--break-lock` | No | Remove stale/crashed lock before mutating |

Success output:
```text
Snapshot <snapshot-id> committed.
Summary: entries=... files=... new=... changed=... deleted=... unchanged=... scanned_bytes=... new_bytes=... skipped=...
```

Partial output may include:
```text
warning: snapshot is partial (N file(s) skipped)
```

Strict abort output:
```text
Backup aborted; no snapshot committed.
skipped: <path>: <reason>
```

---

### List

```powershell
backup-tool list --repo <path>
```

Output:
```text
* <snapshot-id>  <created-at>  complete  entries=<n>  new_bytes=<n>  source=<source>
```

Newest snapshot is marked with `*`. Partial snapshots are marked `[PARTIAL]`.

---

### Info

```powershell
backup-tool info --repo <path>
```

Output:
- JSON repository metadata on stdout
- snapshot/object counts on stderr

Example count line:
```text
snapshots=3 objects=12 last_backup=2026-05-26T13:00:00.000000Z
```

---

### Show

```powershell
backup-tool show <snapshot> --repo <path>
```

`<snapshot>` may be:
- exact snapshot ID
- snapshot JSON filename
- `latest`

Output:
```text
snapshot=<id> status=<status> files=<n> skipped=<n>
{ ... manifest JSON ... }
```

---

### Restore

```powershell
backup-tool restore <snapshot> --repo <path> --to <destination> [--file <relative-path>] [--force] [--safe-symlinks] [--break-lock]
```

| Argument | Required | Description |
|---|---:|---|
| `<snapshot>` | Yes | Snapshot ID, JSON filename, or `latest` |
| `--repo <path>` | Yes | Repository path |
| `--to <destination>` | Yes | Restore target |
| `--file <relative-path>` | No | Restore one file or subtree |
| `--force` | No | Replace existing target after staging succeeds |
| `--safe-symlinks` | No | Reject absolute or parent-escaping symlink targets |
| `--break-lock` | No | Force-remove stale lock |

Success output:
```text
Restored X file(s), Y director(ies), and Z symlink(s) to <destination>
```

Partial symlink output:
```text
warning: restore is partial (N symlink(s) failed)
```

---

### Diff

```powershell
backup-tool diff <snapshot-a> <snapshot-b> --repo <path> [--verbose]
```

Output groups:
```text
Added (N):
Changed (N):
Deleted (N):
Summary: N added, N changed, N deleted, N unchanged
```

With `--verbose`, unchanged paths are also printed.

---

### Verify

```powershell
backup-tool verify <snapshot> --repo <path>
```

Success:
```text
Snapshot <snapshot-id> verified.
```

Failure:
```text
error: <path>: Missing blob: <hash>
```

---

### Check

```powershell
backup-tool check --repo <path> [--repair] [--break-lock]
```

Output:
```text
snapshots=<n> objects=<n> referenced=<n> orphans=<n>
Repository check passed.
```

Warnings may include stale temp files and orphan blobs.

With `--repair`, malformed object paths are moved into `tmp/quarantine/`.

---

### Prune

```powershell
backup-tool prune --repo <path> --keep N [--dry-run] [--gc] [--break-lock]
```

Deletes old snapshot manifests and digest sidecars, keeping the newest `N` manifests.

Output:
```text
Deleted N snapshot(s).
<snapshot-id>
```

With `--gc`:
```text
Deleted N blob(s); bytes=<bytes>.
```

Without `--gc`, after deleting manifests:
```text
hint: run `backup-tool gc --repo <path>` or use --gc to reclaim blob space.
```

---

### Garbage Collection

```powershell
backup-tool gc --repo <path> [--dry-run] [--aggressive] [--break-lock]
```

Deletes blobs not referenced by surviving manifests.

Output:
```text
Deleted N blob(s); bytes=<bytes>.
```

Aggressive mode also handles malformed object paths and stale manifest/lock temp files.

---

### Manifest digest migration

```powershell
backup-tool migrate manifest-digests --repo <path> [--break-lock]
```

Output:
```text
Migrated N manifest digest sidecar(s).
```

or:
```text
No manifests required digest migration.
```

---

## Exit Codes

| Code | Meaning |
|---:|---|
| 0 | Success |
| 1 | General error, invalid arguments, repository error, or manifest error |
| 2 | Integrity or verification failure |
| 3 | Backup completed with skipped files, restore partial, or strict mode aborted |
| 4 | Unexpected internal error |
| 5 | Could not acquire repository lock |

---

## Input Contracts

### Source directory

`backup` requires an existing directory. It rejects:
- missing source
- source that equals the repository directory
- source inside the repository directory

If the repository is inside the source tree, the repository path is auto-excluded and a warning is emitted.

---

### Manifest paths

Manifest paths are normalized as POSIX-style relative paths. Invalid paths include:
- empty path
- `.`
- absolute path
- path containing `..`
- path containing empty components

---

### Exclude patterns

Patterns are normalized to `/`. Patterns containing `..` are rejected.

Examples:

| Pattern | Meaning |
|---|---|
| `*.tmp` | Match basename at any depth |
| `__pycache__` | Match that directory name and its contents |
| `build/` | Match paths under build prefix |
| `tests/foo.py` | Match exact manifest path |
| `dir/*.py` | Match direct children under `dir/` |

---

### Symlink targets

Default restore recreates recorded targets exactly. Safe symlink mode rejects:
- empty targets
- absolute targets
- Windows drive-letter targets
- UNC-style targets
- targets containing `..`

---

## Output Contracts

### Repository metadata

`repo.json` contains:
- `version`
- `created_at`
- `hash_algorithm`
- `storage`
- `object_layout`
- `chunking`

Validated fields include version, hash algorithm, storage model, object layout, and chunking scheme.

---

### Manifest JSON

Each manifest contains:
- `version`
- `snapshot_id`
- `created_at`
- `source`
- `hash_algorithm`
- `status`
- `stats`
- `files`
- `skipped`

`files` maps manifest paths to file, directory, or symlink entries.

---

### Blob storage

Blob path:
```text
objects/<first-two-hash-chars>/<full-sha256>
```

Large-file chunks are stored as individual blobs and referenced by their hashes in the manifest entry's `chunks` list.

---

## Environment Variables

The core tool does not require runtime environment variables.

Development and CI are configured through:
- Python version
- pytest settings in `pyproject.toml`
- coverage settings in `pyproject.toml`
- ruff settings in `pyproject.toml`
- mypy settings in `pyproject.toml`

---

## Side Effects

| Operation | Side Effect |
|---|---|
| `init` | Creates repository directories, `repo.json`, and lock during init |
| `backup` | Reads source, writes staged blobs, promotes objects, writes manifest and digest sidecar |
| `backup --dry-run` | Reads/hashes source but does not commit snapshot or blobs |
| `restore` | Writes staging tree and replaces destination if allowed |
| `verify` | Reads blobs and manifests only |
| `check` | Reads repository and may report warnings/errors |
| `check --repair` | Moves malformed object paths into quarantine |
| `prune` | Deletes old manifests and sidecars |
| `gc` | Deletes unreferenced blobs and possibly stale temp files |
| `migrate manifest-digests` | Writes missing `.sha256` sidecars |
| `--break-lock` | Removes a lock file before acquiring lock |

---

## Usage Examples

### First backup

```powershell
backup-tool init --repo .mybackup
backup-tool backup . --repo .mybackup --exclude .mybackup
backup-tool list --repo .mybackup
```

---

### Verify and restore

```powershell
backup-tool verify latest --repo .mybackup
backup-tool restore latest --repo .mybackup --to restored
```

---

### Partial restore

```powershell
backup-tool restore latest --repo .mybackup --to restored-notes --file notes/
```

---

### Retention with space reclamation

```powershell
backup-tool prune --repo .mybackup --keep 5 --gc
```

---

### Check and repair

```powershell
backup-tool check --repo .mybackup
backup-tool check --repo .mybackup --repair
```

---

### One-time manifest digest migration

```powershell
backup-tool migrate manifest-digests --repo .mybackup
```

---

## Public Python Interfaces

Primary interfaces:
- `Repository`
- `Repository.init`
- `Repository.backup`
- `Repository.restore`
- `Repository.verify`
- `Repository.check`
- `Repository.prune`
- `Repository.gc`
- `Manifest`
- `FileEntry`
- `ObjectStore`
- `SnapshotEngine`

---

*Constitution reference: Article 4 (input/output boundaries), Article 6 (verification), and Article 8 (understandable and verifiable work).* 

---


# Runbook
## App — Backup Tool
**Local Resilience Group | Document 4 of 5**

---

## Requirements

### Runtime

- Python 3.11 or newer
- Local filesystem access
- Sufficient disk space for repository objects and snapshots
- Permission to read source files
- Permission to write repository and restore destination

Runtime package dependencies:
```text
none
```

### Development

- pytest
- coverage
- ruff
- mypy
- setuptools editable install

---

## Installation

### Development install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .
```

### Verify console script

```powershell
backup-tool version
```

### Module invocation fallback

```powershell
python -m backup_tool.cli version
```

---

## Standard Operating Procedures

### Initialize repository

```powershell
backup-tool init --repo .mybackup
```

Expected:
```text
Initialized repository: .mybackup
```

Verify layout:
```text
.mybackup/repo.json
.mybackup/objects/
.mybackup/snapshots/
.mybackup/tmp/
```

---

### Create a snapshot

```powershell
backup-tool backup C:\Projects\docs --repo D:\Backups\docs-backup
```

If repository is inside source:

```powershell
backup-tool backup . --repo .mybackup --exclude .mybackup
```

Expected:
```text
Snapshot <snapshot-id> committed.
Summary: entries=... files=... new=... changed=... deleted=... unchanged=... scanned_bytes=... new_bytes=... skipped=...
```

---

### Dry-run a backup

```powershell
backup-tool backup C:\Projects\docs --repo D:\Backups\docs-backup --dry-run --verbose
```

Expected:
```text
Dry run: snapshot <snapshot-id> was not committed.
```

No manifest is saved.

---

### Enforce strict backup

```powershell
backup-tool backup C:\Projects\docs --repo D:\Backups\docs-backup --strict
```

If files are skipped:
```text
Backup aborted; no snapshot committed.
```

Exit code:
```text
3
```

---

### List snapshots

```powershell
backup-tool list --repo .mybackup
```

Expected:
```text
* <latest-snapshot-id> ...
```

Partial snapshots show `[PARTIAL]`.

---

### Inspect repository metadata

```powershell
backup-tool info --repo .mybackup
```

Use this after initialization, after large backup runs, and before/after retention operations.

---

### Inspect one manifest

```powershell
backup-tool show latest --repo .mybackup
```

Use this to confirm snapshot status, stats, skipped files, file entry types, hashes, chunks, and source path.

---

### Restore full snapshot

```powershell
backup-tool restore latest --repo .mybackup --to restored
```

If destination exists and is non-empty, use a different destination or explicitly force:

```powershell
backup-tool restore latest --repo .mybackup --to restored --force
```

---

### Restore one file or subtree

```powershell
backup-tool restore latest --repo .mybackup --to restored-notes --file notes/todo.txt
backup-tool restore latest --repo .mybackup --to restored-notes --file notes/
```

---

### Safe symlink restore

```powershell
backup-tool restore latest --repo .mybackup --to restored --safe-symlinks
```

Use this when restoring an untrusted or externally supplied repository.

---

### Compare snapshots

```powershell
backup-tool diff <snapshot-a> <snapshot-b> --repo .mybackup
backup-tool diff <snapshot-a> <snapshot-b> --repo .mybackup --verbose
```

---

### Verify one snapshot

```powershell
backup-tool verify latest --repo .mybackup
```

Healthy:
```text
Snapshot <snapshot-id> verified.
```

Unhealthy:
```text
error: <manifest-path>: Missing blob: <hash>
```

---

### Check full repository

```powershell
backup-tool check --repo .mybackup
```

Healthy:
```text
Repository check passed.
```

If orphan blobs are reported:
```powershell
backup-tool gc --repo .mybackup
```

---

### Repair malformed object paths

```powershell
backup-tool check --repo .mybackup --repair
```

Malformed object paths are moved to:
```text
tmp/quarantine/
```

---

### Prune retention

Keep newest five manifests:

```powershell
backup-tool prune --repo .mybackup --keep 5
```

Then reclaim blob storage:

```powershell
backup-tool gc --repo .mybackup
```

Or do both:

```powershell
backup-tool prune --repo .mybackup --keep 5 --gc
```

Dry-run:

```powershell
backup-tool prune --repo .mybackup --keep 5 --dry-run --gc
```

---

### Garbage collect blobs

```powershell
backup-tool gc --repo .mybackup
```

Aggressive cleanup:

```powershell
backup-tool gc --repo .mybackup --aggressive
```

---

### Migrate legacy manifests

```powershell
backup-tool migrate manifest-digests --repo .mybackup
```

Run once if older manifests predate digest sidecars.

---

## Health Checks

### Basic CLI health

```powershell
backup-tool version
```

Expected:
```text
0.1.0
```

---

### Repository metadata health

```powershell
backup-tool info --repo .mybackup
```

Healthy:
- metadata JSON parses
- version is supported
- hash algorithm is `sha256`
- storage is content-addressable
- object layout is `sha256-prefix-2`

---

### Snapshot integrity health

```powershell
backup-tool verify latest --repo .mybackup
```

Healthy:
- exit code 0
- all referenced blobs exist
- all hashes match

---

### Whole-repository health

```powershell
backup-tool check --repo .mybackup
```

Healthy:
- exit code 0
- no metadata errors
- no manifest digest errors
- no missing/hashing errors
- orphan blobs, if any, are only warnings

---

### Restore health

```powershell
backup-tool restore latest --repo .mybackup --to restore-test
```

Then inspect files manually or run downstream validation on restored content.

---

## Expected Outputs

### Backup

```text
Snapshot 2026-05-26T13-00-00-123456Z_abcd1234 committed.
Summary: entries=3 files=2 new=1 changed=0 deleted=0 unchanged=1 scanned_bytes=12 new_bytes=12 skipped=0
```

---

### List

```text
* 2026-05-26T13-00-00-123456Z_abcd1234  2026-05-26T13:00:00.123456Z  complete  entries=3  new_bytes=12  source=C:\Projects\docs
```

---

### Verify partial snapshot

```text
warning: snapshot is partial
Snapshot <snapshot-id> verified.
```

---

### Check with orphan blobs

```text
snapshots=5 objects=12 referenced=10 orphans=2
warning: 2 orphan blob(s) found
hint: run `backup-tool gc --repo .mybackup` to remove unreferenced blobs.
Repository check passed.
```

---

## Known Failure Modes

### Repository is locked

**Symptom:**
```text
lock error: Repository is locked: <repo>/lock
```

**Causes:**
- another command is running
- previous process crashed
- stale lock remains

**Resolution:**
- wait for active command to finish
- run with `--break-lock` only if no process is active
- use verbose backup to see stale lock removal warning

---

### Backup skipped files

**Symptom:**
```text
warning: snapshot is partial (N file(s) skipped)
```

**Causes:**
- permissions issue
- file changed while being read
- unsupported file type
- stat/read failure

**Resolution:**
- inspect verbose skipped output
- close mutating programs
- rerun backup
- use `--strict` when partial snapshots are unacceptable

---

### Strict backup aborted

**Symptom:**
```text
Backup aborted; no snapshot committed.
```

**Resolution:**
- fix skipped-file cause
- remove `--strict` if partial snapshot is acceptable

---

### Missing blob during verify

**Symptom:**
```text
error: path: Missing blob: <hash>
```

**Causes:**
- object file deleted
- disk corruption
- incomplete copy of repository

**Resolution:**
- restore object from another backup of the repository
- use a different snapshot if available
- run `check` to assess full damage

---

### Manifest digest missing

**Symptom:**
```text
Manifest digest sidecar missing
```

**Resolution:**
```powershell
backup-tool migrate manifest-digests --repo .mybackup
```

Only run this for trusted legacy manifests.

---

### Manifest digest mismatch

**Symptom:**
```text
Manifest digest mismatch
```

**Causes:**
- accidental manifest edit
- partial write/copy
- corruption

**Resolution:**
- recover manifest and sidecar from a known-good copy
- remove the affected snapshot only after confirming retention impact

---

### Restore destination exists

**Symptom:**
```text
Destination is not empty
```

**Resolution:**
- choose a new destination
- empty the destination
- use `--force` after confirming the replacement is intended

---

### Unsafe symlink target

**Symptom:**
```text
Unsafe symlink target
```

**Cause:** restore used `--safe-symlinks` and the manifest recorded an absolute or parent-escaping target.

**Resolution:**
- inspect manifest
- restore without `--safe-symlinks` only if the repository is trusted

---

### Orphan blobs after prune

**Symptom:** `check` warns about orphan blobs.

**Resolution:**
```powershell
backup-tool gc --repo .mybackup
```

---

## Troubleshooting Tree

```text
Command failed
  ├── Exit code 5?
  │     ├── Another process running? wait
  │     └── Crashed lock? use --break-lock carefully
  ├── Exit code 2?
  │     ├── Run verify on affected snapshot
  │     ├── Run check on repository
  │     └── recover missing/corrupt blobs or manifests
  ├── Exit code 3?
  │     ├── Backup partial? inspect skipped files
  │     ├── Strict abort? rerun after fixing skips
  │     └── Restore partial? inspect symlink warnings
  ├── Exit code 1?
  │     ├── Invalid args?
  │     ├── Not initialized?
  │     ├── Bad manifest?
  │     └── Unsafe path/pattern?
  └── Exit code 4?
        └── unexpected internal error; rerun with simple repro and inspect filesystem state
```

---

## Dependency Failure Handling

### Runtime dependencies

None.

### Dev dependencies

```powershell
pip install -r requirements-dev.txt
pip install -e .
```

### Symlink tests on Windows

Enable Developer Mode or equivalent symlink privilege. Otherwise, Windows symlink tests may be skipped.

---

## Recovery Procedures

### Recover from stale lock

1. Confirm no backup-tool process is active.
2. Run the intended command with `--break-lock`.
3. Run `backup-tool check --repo <repo>` afterward.

---

### Recover from interrupted backup

1. Run:
   ```powershell
   backup-tool check --repo <repo>
   ```
2. If stale tmp files are reported, run:
   ```powershell
   backup-tool gc --repo <repo> --aggressive
   ```
3. Rerun backup.

---

### Recover disk space after retention

```powershell
backup-tool prune --repo <repo> --keep 5 --gc
```

If prune already ran without GC:

```powershell
backup-tool gc --repo <repo>
```

---

### Recover after corrupt object

1. Identify affected snapshot through `verify` or `check`.
2. Restore missing object from a separate repository backup if available.
3. If unavailable, use an older snapshot that does not reference the corrupt object.
4. Do not run GC until recovery is complete.

---

### Recover after bad manifest edit

1. Stop using the repository.
2. Restore manifest JSON and `.sha256` sidecar from a known-good copy.
3. Run:
   ```powershell
   backup-tool check --repo <repo>
   ```

---

## Maintenance Notes

- Run `verify latest` after important backups.
- Run `check` periodically.
- Run `prune --gc` or `gc` after retention changes.
- Keep the repository out of the source tree when possible.
- If the repository is inside the source, ensure it is excluded.
- Treat public/shared repositories as untrusted unless restored with `--safe-symlinks`.
- Keep separate copies of the backup repository if the data matters.
- Do not edit manifests manually.
- Do not delete objects manually.
- Run migration once for trusted legacy manifests missing digest sidecars.

---

*Constitution reference: Article 6 (behavior verification), Article 5 (trade-off documentation), and Article 8 (verifiable learner work).* 

---


# Lessons Learned
## App — Backup Tool
**Local Resilience Group | Document 5 of 5**

---

## Why This Design Was Chosen

The backup tool was designed around the principle that a local backup system should be understandable on disk. A user can open the repository directory and see metadata, objects, snapshots, temporary staging data, and a lock file. This made content-addressable storage and JSON manifests a natural fit.

The most important design choice was separating the CLI from the library API. `backup-tool` is the operator interface, but `Repository` is the real application boundary. This kept the command parser from becoming the architecture and allowed backup, restore, verify, prune, and garbage collection to be tested as Python behavior.

The second important choice was immutability. Once a snapshot is committed, it is not rewritten. This reduces mental overhead: new backups create new manifests, retention deletes old manifests, and garbage collection deletes objects no surviving manifest references.

The third important choice was honesty about the trust model. The tool verifies blobs and catches accidental manifest corruption, but it does not claim to provide encrypted or signed tamper-proof backups. That limitation is documented rather than hidden.

---

## What Was Intentionally Omitted

**Encryption:** Omitted because secure key management would dominate the project.

**Compression:** Omitted to keep hashing and restore logic transparent.

**Remote storage:** Omitted because the goal is local repository architecture.

**Parallel scanning:** Omitted to keep locking, staging, and ordering simpler.

**Content-defined chunking:** Deferred because fixed-size chunking is easier to explain and test.

**Mtime-only incremental fast path:** Omitted because correctness was prioritized over speed.

**Full filesystem metadata:** Permissions and mtimes are handled where practical, but ACLs, xattrs, owners, and platform-specific metadata are out of scope.

**Manifest signatures:** Digest sidecars catch accidents, not malicious tampering.

---

## Biggest Weakness

The biggest weakness is performance on large trees. The tool walks the whole source tree, materializes the list in memory, sorts it, and re-hashes accepted files. Stable-file detection can read each accepted file multiple times. This is correct and simple, but expensive.

The second weakness is missing security features. There is no encryption, compression, authentication, or remote backend. That makes the project appropriate for local academic backups, not sensitive production backup infrastructure.

The third weakness is the manifest trust model. Digest sidecars detect accidental corruption, but a malicious actor with write access can rewrite both manifest and digest. Stronger trust would require signatures or an append-only external log.

---

## Scaling Considerations

**If datasets grow large:**
- add a scan cache keyed by path, size, mtime, and inode metadata
- avoid materializing the entire tree at once
- stream manifest generation where possible
- add progress reporting
- consider parallel hashing with bounded workers

**If storage efficiency matters:**
- add compression
- consider content-defined chunking
- add object packing for many small blobs

**If security matters:**
- add encryption at rest
- add repository keys and rotation
- add signed manifests
- add secure erase policy for temporary files

**If backups must leave the machine:**
- add remote object-store abstraction
- make upload/download resumable
- separate repository metadata from object transfer
- add network error recovery

**If restore safety becomes stricter:**
- default to safe symlinks
- add dry-run restore planning
- add collision reports
- add file-level checksum report after restore

---

## What the Next Refactor Would Be

1. **Add scan cache** — reduce full-file rehashing for clearly unchanged files while preserving correctness.

2. **Add progress reporting** — show file count, bytes scanned, and current phase during long backups.

3. **Add content-defined chunking experiment** — compare dedupe rate against fixed 1 MiB blocks.

4. **Add manifest signing** — distinguish accidental corruption from malicious rewrite.

5. **Add restore dry-run** — show what would be restored/replaced before writing.

6. **Add repository copy/check command** — safely clone a repository to another disk while verifying objects and manifests.

---

## What This Project Taught

- **Backups are mostly about failure modes.** A happy-path copy command is easy. Handling partial reads, moved files, locks, object corruption, restore safety, and retention is the real design work.

- **Content-addressable storage simplifies integrity.** If the object name is the hash, verification has a natural target.

- **Manifests need strict validation.** Path normalization, entry type rules, digest sidecars, and schema checks prevent small mistakes from becoming unsafe restore behavior.

- **Restore safety matters as much as backup.** Writing to staging first protects the destination from partial restore failures.

- **Locks need ownership tokens.** A process should not delete another process's lock just because a path exists.

- **Simple chunking still teaches a lot.** Fixed chunks introduce composite hashes, chunk lists, dedupe, restore streaming, and verification complexity without a full rolling-hash algorithm.

- **Documentation must state what verification proves.** Verify detects missing and corrupt blobs. It does not prove the repository was not maliciously edited.

- **Tests are essential for file tools.** Edge cases around paths, locks, symlinks, staging, pruning, garbage collection, and partial snapshots are where bugs tend to hide.

---

*Constitution v2.0 checklist: This document satisfies Article 5 (trade-off documentation), Article 6 (verification), and Article 7 (progressive complexity) for Backup Tool.*
