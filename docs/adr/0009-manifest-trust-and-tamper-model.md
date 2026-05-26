# ADR 0009: Manifest Trust and Tamper Model

## Context

Snapshot manifests are plain JSON files stored alongside content-addressed blobs.
Users may assume `verify` proves a snapshot is authentic and complete. It does not.

## Decision

Treat manifests as **trusted metadata** unless an external integrity mechanism is
added later. **`verify` means bit-rot detection, not tamper resistance.** It
checks that referenced blobs exist and match their SHA-256 hashes. It does **not**
detect:

- Files removed from the manifest
- Permission or symlink target edits
- Snapshot status or stats tampering

Symlink targets are recorded and restored **as-is**. A modified manifest can
direct restored symlinks at sensitive absolute paths outside the restore tree.

## Mitigations

- Load-time schema validation rejects malformed hashes, unknown statuses, and
  unsupported hash algorithms.
- Each committed manifest has a side-car `<snapshot>.json.sha256` digest file
  written atomically alongside the JSON. Loads and verify reject digest mismatch.
- `restore --safe-symlinks` rejects absolute targets and targets containing `..`.
- Repository `check` and `verify` flag symlink entries with missing targets.

## Non-goals

- Signed manifests or side-car checksum files (out of scope for this project)
- Encryption or access control on the repository directory

## Consequences

Operators must protect the repository directory filesystem permissions. For
threat models requiring tamper-evident snapshots, wrap manifests with an external
signing or checksum layer.
