# Lessons Learned — Backup Tool

Post-project notes on why the system looks the way it does, what was left out, and what
would change next. ADR detail: [adr/README.md](adr/README.md).

---

## Why this design

**Inspectable on disk.** A backup repository should be understandable without a
database or proprietary format: `repo.json`, content-keyed blobs, JSON manifests, and
a visible lock file.

**Library before CLI.** `Repository` is the real boundary; `backup-tool` is a thin
operator surface. That kept backup, restore, verify, and GC testable without subprocess
overhead and stopped argparse from becoming the architecture.

**Immutability by default.** Committed manifests are not rewritten. New backups append
history; retention deletes old manifests; GC drops unreferenced blobs. Mental model stays
simple.

**Honest trust model.** SHA-256 objects give strong bit-rot detection. Digest sidecars
catch accidental manifest edits. Neither replaces encryption or signed manifests—we
document that gap ([ADR 0009](adr/0009-manifest-trust-and-tamper-model.md)).

---

## What we intentionally omitted

| Omitted | Reason |
|---------|--------|
| Encryption | Key management would dominate scope |
| Compression | Keeps hash/restore paths transparent |
| Remote storage | Goal is local repository mechanics |
| Parallel scan | Simpler locking and ordering |
| Content-defined chunking | Fixed 1 MiB blocks are easier to teach and test |
| mtime-only incremental | Correctness over speed in V1 |
| Full ACL/xattr/owner backup | Platform metadata explosion |
| Signed manifests | Sidecars address accidents, not adversaries |

---

## Biggest weaknesses

1. **Performance on large trees** — full walk materialized and sorted; every included
   file re-hashed; stable-read can touch a file three times. Correct, expensive.
2. **No production security story** — no encryption, auth, or remote backend; suitable
   for local academic datasets, not regulated production backup.
3. **Manifest trust ceiling** — a writer who controls the repo can rewrite manifest and
   sidecar together; `verify` will still validate blobs against a tampered catalog.

---

## Scaling directions (V2+)

Documented in [ADR 0012](adr/0012-scaling-and-incremental-scan-v2.md):

- Generator-based walk instead of full in-memory file lists
- Optional mtime+size fast path with content hash as authority
- Progress reporting and bounded parallel hashing
- Content-defined chunking experiment vs fixed blocks
- Signed manifests or external audit log
- Remote object-store abstraction with resumable transfer

---

## Next refactors (priority order)

1. Scan cache — skip full rehash when prior manifest entry matches size/mtime (with
   full-hash fallback).
2. Progress reporting — phase, file count, bytes scanned.
3. Restore dry-run — planned writes and collisions before staging.
4. Repository clone command — verified copy to another disk.
5. Manifest signing — separate accidental corruption from malicious rewrite.

---

## What building this taught

- **Backups are failure-mode engineering.** Partial reads, concurrent writers, locks,
  orphan blobs, prune vs GC, and restore destination policy dominate the design space.
- **Content-addressing simplifies verification.** Object path equals expected hash.
- **Manifest validation is security-adjacent.** Path normalization, entry typing, and
  chunk rules prevent subtle restore escapes.
- **Restore safety equals backup safety.** Staging-before-replace avoids corrupting an
  existing destination on partial failure.
- **Locks need tokens.** Releasing a lock must not delete another process's active lock.
- **Chunking teaches composite integrity.** File hash must match chunk chain—good step
  before rolling-hash systems.
- **Say what `verify` proves.** Missing blob and hash mismatch yes; manifest tamper no.
- **File-tool bugs live in edges.** Symlinks, Windows privileges, strict/partial snapshots,
  concurrent lock contention, and staging promotion need explicit tests.

---

## Test and quality bar

The project ships broad pytest coverage (chunking, locks, symlinks, staging, prune+GC,
CLI exit codes), 85% line coverage on `backup_tool`, ruff, mypy, and cross-platform CI.
That investment matched the edge-case surface area more than the happy-path copy logic.
