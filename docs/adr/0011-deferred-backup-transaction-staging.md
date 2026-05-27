# ADR 0011: Deferred Backup Transaction Staging

## Status

Accepted (implemented in Version 1).

## Decision

Non-dry-run backups write new blobs under `tmp/staging/<snapshot-id>/` during the scan.
`ObjectStore.promote_staging()` moves only hashes referenced by the committed manifest
into `objects/`. On strict abort, failed scan, or dry-run, staging is discarded.

## Reason

Direct writes to `objects/` during an in-progress backup leave orphan blobs when the
manifest is never committed (strict mode, crash, or skipped files). Staging gives a
two-phase commit: **stage → validate manifest → promote**, aligned with “snapshot commits
only after referenced blobs exist.”

## Behavior

- `begin_staging(snapshot_id)` at backup start
- `put_file` / `put_bytes` target staging paths while staging is active
- `promote_staging(referenced_hashes)` on successful commit
- `discard_staging(snapshot_id)` on abort or after promotion cleanup
- Aggressive `gc` and `check` can remove orphan staging dirs under `tmp/staging/`

## Trade-offs

- Extra disk use under `tmp/` during long backups
- Promotion must skip blobs already present and valid in `objects/`
- Crash mid-backup may leave a staging directory until GC or aggressive cleanup

## Non-goals

- Cross-repository transactions
- Streaming promotion before the full referenced-hash set is known

## Related

- [ADR 0006](0006-never-destructive-by-default.md) — never destructive by default
- [TDD](../TDD.md) — backup data flow
