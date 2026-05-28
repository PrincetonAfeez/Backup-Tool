# Technical Design Document — Backup Tool

**Status:** Accepted · **Version:** 0.1.0 · **Repository format:** 1

## Overview

Local Python package and CLI for incremental, verifiable filesystem snapshots. File bytes
live in SHA-256 content-addressed objects; snapshot metadata lives in immutable JSON
manifests with digest sidecars.

| Item | Value |
|------|-------|
| Package | `backup_tool` |
| Console | `backup-tool` |
| Module CLI | `python -m backup_tool` (also `python -m backup_tool.cli`) |
| Python | 3.11+ |
| Runtime deps | none |
| Hash | SHA-256 |
| Chunking | Fixed 1 MiB blocks for files over 1 MiB |

Public API boundary: `Repository` (see [ADR 0010](adr/0010-module-layout.md)).

---

## Repository layout

```text
<repo>/
  repo.json
  lock
  objects/<aa>/<full-sha256>
  snapshots/<snapshot-id>.json
  snapshots/<snapshot-id>.json.sha256
  tmp/staging/<snapshot-id>/...
  tmp/quarantine/          (check --repair)
```

---

## Data flows

### Init

```text
init --repo PATH
  → Repository.init
  → reject existing repo.json; optional --allow-nonempty
  → RepositoryLock → mkdir objects, snapshots, tmp
  → atomic write repo.json
```

### Backup

```text
backup SRC --repo PATH
  → validate metadata; reject src inside repo; auto-exclude repo in tree
  → RepositoryLock
  → SnapshotEngine.build_snapshot
        → begin_staging(snapshot_id)
        → walk (followlinks=False); normalize POSIX-relative paths
        → directories, symlinks, stable-read files (double-hash + store pass)
        → small files → single blob; large → fixed chunks
        → diff vs previous manifest; build Manifest
        → promote_staging (referenced hashes) or discard on abort
  → ensure blobs exist → ManifestStore.save + sidecar
```

Non-dry-run backups stage under `tmp/staging/<snapshot-id>/` and promote only on
success ([ADR 0011](adr/0011-deferred-backup-transaction-staging.md)). Strict mode
discards staging and commits no manifest when any file is skipped.

### Restore

```text
restore SNAPSHOT --to DEST [--file PATH] [--force] [--safe-symlinks]
  → lock → load manifest
  → create .restore-<snapshot-id>.* temp dir beside destination.parent
  → restore blobs/chunks, dirs, symlinks into staging dir
  → restore mtime/mode where possible
  → with --file: merge selected paths into --to (unrelated files preserved)
  → without --file: atomically replace destination (refuse non-empty dest without --force)
```

Restore staging lives next to the destination (`tempfile.mkdtemp(..., dir=destination.parent,
prefix=".restore-<snapshot-id>.")`), not under the repository `tmp/` tree.

### Verify / check

- **`verify`:** per-snapshot blob presence, chunk chain, composite file hash, size.
- **`check`:** repo metadata, all manifests + sidecars, references, orphans, malformed
  object paths, stale tmp.
- **`check --repair`:** quarantines malformed object paths, quarantines unloadable snapshot
  manifests, removes orphan manifest digest sidecars, removes stale blob/manifest/lock temp
  files, and removes orphan staging directories under `tmp/staging/`. `gc --aggressive` can
  also clean stale temp artifacts while performing garbage collection.

### Retention

```text
prune --keep N [--gc]  → delete old manifest + sidecar
gc                     → delete blobs unreferenced by surviving manifests
```

`prune` alone does not reclaim blob space.

---

## Module structure

```text
backup_tool/
  repository.py      # facade: init, backup, restore, prune, gc, check, verify
  snapshot_engine.py # walk, stable-read, restore staging
  manifest.py        # FileEntry, Manifest, ManifestStore
  object_store.py    # put, promote_staging, blob paths
  chunking.py        # threshold, hash/store/restore/verify file content
  staging.py         # snapshot id + stat validation
  lock.py            # RepositoryLock + stale recovery
  verify.py          # verify_manifest, check_repository
  gc.py              # gc_unlocked
  paths.py           # normalization, exclude, restore safety
  atomic.py          # temp file + replace
  cli.py             # thin argparse → Repository
```

**Dependency spine:** `cli` → `repository` → (`snapshot_engine`, `manifest`, `object_store`,
`verify`, `gc`, `lock`, `diff`).

---

## Core types

### `FileEntry`

`type`: `file` | `symlink` | `directory`. Files require `hash`; large files add `chunks[]`.
Symlinks require `target`; directories forbid file-only fields.

### `Manifest`

Immutable snapshot: `snapshot_id`, `created_at`, `source`, `status` (`complete` |
`partial` | `dry-run`), `stats`, `files`, `skipped`. Committed via atomic write +
`.sha256` sidecar.

### Results

| Type | Role |
|------|------|
| `SnapshotResult` | backup: manifest, diff, committed, warnings |
| `RestoreResult` | restore counts; `partial` if symlinks failed |
| `VerifyResult` | per-path errors |
| `CheckResult` | repo-wide errors/warnings, repair state |
| `GCResult` | deleted/kept blobs, tmp cleanup |

---

## Concurrency and errors

- Single process per mutating operation; no threads/async.
- Lock file: PID, timestamp, token; stale PID auto-clear; `--break-lock` for ops recovery.
- CLI exit codes: 0 success, 1 general, 2 integrity, 3 partial/strict, 4 internal, 5 lock.

---

## Verification (engineering)

| Layer | Tooling |
|-------|---------|
| Unit/integration | pytest (`tests/`), 85% coverage fail-under on `backup_tool` |
| Static | ruff, mypy (`backup_tool/`) |
| CI | Ubuntu + Windows, Python 3.11–3.12 ([`.github/workflows/tests.yml`](../.github/workflows/tests.yml)) |

JSON schemas: [`Schema/`](../Schema/).

---

## Known limits (V1)

Full tree materialized in memory; every included file re-hashed (no mtime fast path);
stable-read may read a file up to three times; no encryption, compression, or remote
backend. See [ADR 0012](adr/0012-scaling-and-incremental-scan-v2.md) and
[Lessons learned](LESSONS_LEARNED.md).
