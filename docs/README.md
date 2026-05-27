# Documentation

Backup Tool is a standard-library Python package and CLI for local, content-addressed
snapshots. Use this index to navigate design notes, operations, and portfolio context.

| Document | Audience | Purpose |
|----------|----------|---------|
| [README](../README.md) | Everyone | Install, commands, safety rules, limitations |
| [ADR index](adr/README.md) | Contributors | Per-decision records (0001–0012) |
| [Technical design (TDD)](TDD.md) | Implementers | Data flows, modules, types, concurrency |
| [Runbook](RUNBOOK.md) | Operators | SOPs, health checks, failure modes, recovery |
| [Lessons learned](LESSONS_LEARNED.md) | Reviewers | Trade-offs, omissions, scaling, refactors |
| [Case study](CASE_STUDY.md) | Portfolio | Concise narrative of design decisions |
| [Release checklist](RELEASE.md) | Maintainers | Version bump and pre-tag checks |

## Architecture at a glance

```text
Source tree  ──backup──►  Repository
                            ├── repo.json      (format metadata)
                            ├── objects/       (SHA-256 blobs + chunks)
                            ├── snapshots/     (immutable JSON + .sha256 sidecars)
                            ├── tmp/staging/   (in-flight blobs per snapshot)
                            └── lock           (exclusive mutation guard)
```

**Core invariants:** source is never modified; manifests are immutable after commit;
blobs are promoted only after a successful snapshot; `verify` checks object integrity,
not manifest authenticity (see [ADR 0009](adr/0009-manifest-trust-and-tamper-model.md)).

## ADR quick map

| Theme | ADRs |
|-------|------|
| Storage & dedup | [0001](adr/0001-content-addressable-storage.md), [0003](adr/0003-whole-file-dedup.md), [0008](adr/0008-fixed-size-block-chunking.md) |
| Change detection | [0002](adr/0002-pure-content-hash-change-detection.md), [0012](adr/0012-scaling-and-incremental-scan-v2.md) |
| Manifests & trust | [0004](adr/0004-manifest-as-shallow-merkle-structure.md), [0009](adr/0009-manifest-trust-and-tamper-model.md) |
| Safety & ops | [0005](adr/0005-hashing-not-encryption.md), [0006](adr/0006-never-destructive-by-default.md), [0007](adr/0007-repository-locking.md), [0011](adr/0011-deferred-backup-transaction-staging.md) |
| Code layout | [0010](adr/0010-module-layout.md) |

Legacy monolithic course packet: [backup_tool_docs.md](../backup_tool_docs.md) (index only).
