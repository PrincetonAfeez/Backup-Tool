# ADR 0002: Pure Content-Hash Change Detection

## Decision

Re-hash included files on every backup run.

## Reason

The content hash is the only authority for whether content changed. mtime and
size can be useful optimizations, but they are not the source of truth.

## Trade-off

Large trees are slower to scan. An mtime + size fast path can be layered on top
later.

## Low-resolution mtime caveat

The backup stability check compares ``st_mtime_ns`` before and after hashing.
Filesystems with coarse timestamp granularity (FAT, some SMB shares) may report
the same mtime even when content changed between reads, causing a false
"unchanged" classification or a skip with "file changed while being read".
Content hashes remain authoritative once a file is read successfully; operators
on low-resolution filesystems should treat unexpected skips as a signal to
re-run the backup.
