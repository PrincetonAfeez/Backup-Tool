# ADR 0008: Fixed-Size Block Chunking

## Decision

Files larger than 1 MiB are stored as a sequence of fixed-size content-addressed
chunks. Smaller files remain whole-file blobs for backward-compatible simplicity.

## Reason

Block-level chunking reduces storage amplification when large files change
slightly. Identical chunks deduplicate across files and snapshots while keeping
the manifest format easy to explain for an academic project.

## Trade-off

Chunking adds manifest complexity (`chunks` arrays), restore assembly logic, and
GC reference tracking per chunk. Rolling hash / content-defined chunking is out
of scope.
