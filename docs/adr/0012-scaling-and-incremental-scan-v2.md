# ADR 0012: Scaling and Incremental Scan (Version 2)

## Decision

Version 1 materializes the full source walk in memory and re-hashes every
included file on each backup. Document this limit; defer generator-based walks
and mtime+size fast paths to Version 2.

## Generator-based walk (J7)

Replace `_walk_source` list materialization with a generator that yields entries
in sorted order. This reduces peak memory for large trees without changing the
manifest format.

## mtime+size fast path (ADR 0002 follow-up)

Layer an optional optimization: skip re-hashing when size and mtime match the
previous manifest entry. Content hash remains authoritative when the fast path
does not apply or when the operator requests a full scan.

## Non-goals (Version 1)

Parallel backup, compression, encryption, and distributed repositories.
