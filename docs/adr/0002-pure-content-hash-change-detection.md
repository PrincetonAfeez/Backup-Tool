# ADR 0002: Pure Content-Hash Change Detection

## Decision

Re-hash included files on every backup run.

## Reason

The content hash is the only authority for whether content changed. mtime and
size can be useful optimizations, but they are not the source of truth.

## Trade-off

Large trees are slower to scan. An mtime + size fast path can be layered on top
later.
