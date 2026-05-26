# ADR 0004: Manifest as a Shallow Merkle Structure

## Decision

Each snapshot manifest maps relative paths to content hashes.

## Reason

The manifest points to hashed content, giving the project the same basic shape
as Git object references without adding recursive tree objects.

## Chunked file entries

Large files add a `chunks` array alongside the whole-file `hash`. The top-level
`hash` is the SHA-256 of the complete file content; `chunks` lists the
content-addressed block hashes used to store and restore the file. Verification
checks both the assembled file hash and each chunk blob.

## Trade-off

Directory tree objects are not modeled separately in Version 1.

## Related ADRs

- [ADR 0008](0008-fixed-size-block-chunking.md) — 1 MiB threshold, blob layout,
  and orphan blobs from partial backups.
