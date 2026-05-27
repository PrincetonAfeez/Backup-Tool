# Architecture Decision Records

Per-decision notes for the Backup Tool. Each ADR states **decision**, **reason**, and
**trade-offs** (or **consequences** where noted). Narrative summary: [Case study](../CASE_STUDY.md).

| ADR | Title | Theme |
|-----|-------|-------|
| [0001](0001-content-addressable-storage.md) | Content-addressable storage | Storage |
| [0002](0002-pure-content-hash-change-detection.md) | Pure content-hash change detection | Incremental |
| [0003](0003-whole-file-dedup.md) | Whole-file deduplication | Dedup |
| [0004](0004-manifest-as-shallow-merkle-structure.md) | Manifest as a shallow Merkle structure | Manifests |
| [0005](0005-hashing-not-encryption.md) | Hashing, not encryption | Security scope |
| [0006](0006-never-destructive-by-default.md) | Never destructive by default | Safety |
| [0007](0007-repository-locking.md) | Repository locking | Concurrency |
| [0008](0008-fixed-size-block-chunking.md) | Fixed-size block chunking | Large files |
| [0009](0009-manifest-trust-and-tamper-model.md) | Manifest trust and tamper model | Integrity |
| [0010](0010-module-layout.md) | Module layout (gc, verify, metadata) | Code structure |
| [0011](0011-deferred-backup-transaction-staging.md) | Deferred backup transaction staging | Transactions |
| [0012](0012-scaling-and-incremental-scan-v2.md) | Scaling and incremental scan (V2) | Future work |

**How to read:** start with 0001, 0004, 0006, and 0009 for the core model; add 0008 and 0011
for backup mechanics; use 0012 for deliberate V1 limits.
