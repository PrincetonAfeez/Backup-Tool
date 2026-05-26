# ADR 0007: Repository Locking

## Decision

All repository operations that read or mutate snapshot metadata, manifests, or
object-store references acquire the same exclusive lock file. Stale locks whose
recorded PID is no longer alive are cleared automatically. Users may pass
`--break-lock` to force removal when manual cleanup is required.

This includes read-only commands (`list`, `diff`, `verify`, `check`) and
dry-run backups, which still consult `manifest_store.latest()` and must not
race with concurrent prune, garbage collection, or backup commits.

## Reason

Concurrent backup, prune, or garbage collection operations can invalidate each
other's assumptions. Read commands observing manifests mid-mutation can surface
transient errors or inconsistent previews. A single exclusive lock keeps the
academic tool simple while preserving coherent repository views.

## Lock file durability

Lock acquisition creates an exclusive placeholder, writes the full payload to a
temporary file, fsyncs it, and atomically replaces the lock path. Failed writes
remove the partial lock so stale-lock recovery does not stall on an empty file.

## Trade-off

`--break-lock` can remove a lock held by a live process if used carelessly.
Block-level chunking and distributed locking remain out of scope.
