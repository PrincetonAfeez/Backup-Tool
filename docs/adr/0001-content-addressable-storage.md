# ADR 0001: Content-Addressable Storage

## Decision

Store file contents by SHA-256 hash instead of copying each snapshot into a full
timestamped directory.

## Reason

Content-addressable storage makes deduplication and incremental snapshots a
property of the storage model. Identical content is stored once and referenced by
many manifests.

## Trade-off

Whole-file blobs remain the default for small files. Large files use fixed-size
block chunking (see ADR 0008) to limit storage amplification on small edits.
