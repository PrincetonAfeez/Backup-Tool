# ADR 0003: Whole-File and Block Deduplication

## Decision

Deduplicate small files as whole blobs. Deduplicate large files as fixed-size
content-addressed chunks once they exceed 1 MiB.

## Reason

Whole-file hashing stays simple for typical project files. Block hashing teaches
deduplication trade-offs without adopting rolling-hash chunking.

## Trade-off

The chunk threshold is fixed, not content-defined. Boundary shifts still store
new chunks when file size crosses the threshold.
