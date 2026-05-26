# ADR 0007: Repository Locking

## Decision

Repository-mutating operations acquire a simple lock file. Stale locks whose
recorded PID is no longer alive are cleared automatically. Users may pass
`--break-lock` to force removal when manual cleanup is required.

## Reason

Concurrent backup, prune, or garbage collection operations can invalidate each
other's assumptions. Automatic stale-lock recovery reduces operational friction
for a local academic tool while preserving exclusive access during normal use.

## Trade-off

`--break-lock` can remove a lock held by a live process if used carelessly.
Block-level chunking and distributed locking remain out of scope.
