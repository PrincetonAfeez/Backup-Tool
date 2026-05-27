# Case Study — Local Content-Addressable Backup Tool

**Role:** Solo implementation · **Stack:** Python 3.11+, stdlib only · **Scope:** Library + CLI, ~85% tested core

## Problem

Build a command-line backup system that demonstrates real filesystem architecture—incremental
snapshots, deduplication, integrity checks, retention, and safe restore—without cloud APIs,
databases, or third-party runtime dependencies. The result had to be inspectable on disk and
defensible in design reviews.

## Constraints

- Standard-library runtime only (educational transparency).
- Local repository on a single machine; small to medium trees.
- Operators may script the CLI; developers must test logic without subprocesses.
- Must not mutate backup sources; must not corrupt restore targets on partial failure.

## Solution shape

A **content-addressable object store** plus **immutable JSON manifests**:

```text
backup SRC  →  hash & stage blobs  →  promote  →  commit manifest + digest sidecar
restore     →  stage tree  →  verify blobs  →  atomic replace destination
```

Unchanged bytes reuse existing objects; large files split into 1 MiB chunks so partial
edits dedupe at block granularity ([ADR 0008](adr/0008-fixed-size-block-chunking.md)).

## Design decisions (and why)

| Decision | Rationale | Cost accepted |
|----------|-----------|---------------|
| **SHA-256 CAS** ([0001](adr/0001-content-addressable-storage.md)) | Dedup and verify share one naming scheme | No encryption at rest |
| **Immutable manifests** ([0004](adr/0004-manifest-as-shallow-merkle-structure.md)) | Audit trail; retention = delete, not mutate | Many JSON files vs one DB |
| **Content-hash diff** ([0002](adr/0002-pure-content-hash-change-detection.md)) | Correct change detection without trusting mtime | Full rehash every backup |
| **Stable-read (2× hash + store)** | Avoid committing files that change mid-read | Up to 3 reads per file |
| **Staging then promote** ([0011](adr/0011-deferred-backup-transaction-staging.md)) | No orphan blobs after strict abort | Extra tmp I/O |
| **Restore via staging** ([0006](adr/0006-never-destructive-by-default.md)) | Destination not half-written on failure | Requires empty dest or `--force` |
| **Lock file + token** ([0007](adr/0007-repository-locking.md)) | Portable mutual exclusion | Operator must handle stale locks |
| **Digest sidecars** ([0009](adr/0009-manifest-trust-and-tamper-model.md)) | Catch accidental manifest damage | Not tamper-proof against repo writer |
| **`Repository` API** ([0010](adr/0010-module-layout.md)) | Testable domain core; thin CLI | More modules than a one-file script |

## What shipped

- Commands: `init`, `backup`, `list`, `info`, `show`, `restore`, `diff`, `verify`, `check`,
  `prune`, `gc`, `migrate manifest-digests`
- Partial and strict backup modes; exclude patterns; dry-run
- Fixed-size chunking above 1 MiB; symlink preserve + `--safe-symlinks`
- CI: pytest + coverage (85% floor), ruff, mypy on Ubuntu/Windows, Python 3.11–3.12

## Outcomes

- Repositories are **human-readable**: open `snapshots/*.json` and `objects/ab/…` without
  special tools.
- **Incremental backups** store only new/changed blob content; unchanged files reference
  existing hashes.
- **Operational clarity**: `verify` vs `check` vs `prune` vs `gc` each have a documented job;
  exit codes distinguish lock, integrity, and partial success.

## Honest limits (portfolio framing)

This is a **local resilience / systems-learning** project, not a Borg/Restic competitor.
There is no encryption, compression, parallel pipeline, or remote replication. Performance
trade-offs (full-tree memory, triple-read stable files) were chosen for correctness and
clarity in V1, with V2 paths captured in [ADR 0012](adr/0012-scaling-and-incremental-scan-v2.md).

## If I extended it

First increment: scan cache with content-hash fallback ([ADR 0012](adr/0012-scaling-and-incremental-scan-v2.md)).
Then progress UX and manifest signing for stronger trust stories—without collapsing the
on-disk inspectability that made the project useful for teaching repository design.

## Further reading

- [Documentation index](README.md)
- [Technical design](TDD.md)
- [Runbook](RUNBOOK.md)
- [Lessons learned](LESSONS_LEARNED.md)
- [ADR index](adr/README.md)
