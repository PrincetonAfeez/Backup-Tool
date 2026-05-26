# ADR 0011: Deferred Backup Transaction Staging

## Decision

Version 1 stores blobs in `objects/` as files are hashed during the scan.
Strict mode aborts the manifest commit when any file is skipped, leaving orphan
blobs until `gc` reclaims them.

## Reason

Staging every new blob under `tmp/` and promoting to `objects/` only after a
successful full scan is the architecturally clean contract (transactional
backup). It adds complexity around promotion, crash recovery, and deduplication
against already-committed objects.

## Version 2 direction

Consider a two-phase commit: stage in `tmp/staging/<snapshot-id>/`, promote on
success, and sweep staging directories on abort or crash.

## Non-goals (Version 1)

Atomic multi-file backup transactions beyond manifest+blob existence ordering.
