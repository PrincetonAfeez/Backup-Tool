# Runbook — Backup Tool

Operator guide for local repositories. Design background: [TDD](TDD.md), [ADRs](adr/README.md).

## Requirements

| | |
|-|-|
| Python | 3.11+ |
| Runtime packages | none |
| Disk | Space for objects + manifests + staging |
| Permissions | Read source; write repo and restore destination |

**Dev:** `pip install -r requirements-dev.txt && pip install -e .`

---

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e .
backup-tool version
```

Fallback: `python -m backup_tool.cli version`

---

## Standard procedures

### 1. Initialize

```powershell
backup-tool init --repo .mybackup
```

Expect: `Initialized repository: .mybackup` and `repo.json`, `objects/`, `snapshots/`, `tmp/`.

### 2. Backup

```powershell
backup-tool backup C:\Projects\docs --repo D:\Backups\docs-backup
```

If the repo lives inside the source tree:

```powershell
backup-tool backup . --repo .mybackup --exclude .mybackup
```

| Flag | Use when |
|------|----------|
| `--dry-run` | Preview diff without commit |
| `--strict` | Abort if any file skipped (exit 3, no snapshot) |
| `--verbose` | Skipped paths, diff detail, stale-lock notice |
| `--break-lock` | Crashed prior process left `lock` |

### 3. Inspect

```powershell
backup-tool list --repo .mybackup
backup-tool info --repo .mybackup
backup-tool show latest --repo .mybackup
```

`*` = newest snapshot; `[PARTIAL]` = skipped files recorded.

### 4. Verify and check

```powershell
backup-tool verify latest --repo .mybackup
backup-tool check --repo .mybackup
```

Run `verify` after important backups; run `check` periodically and before/after retention.

### 5. Restore

```powershell
backup-tool restore latest --repo .mybackup --to restored
backup-tool restore latest --repo .mybackup --to restored --file notes/todo.txt
```

Use `--force` only when replacing an existing non-empty destination is intentional.
Use `--safe-symlinks` for untrusted repositories.

### 6. Retention and space

```powershell
backup-tool prune --repo .mybackup --keep 5 --gc
```

Or prune then `backup-tool gc --repo .mybackup`. Dry-run: add `--dry-run`.

### 7. Legacy manifests

```powershell
backup-tool migrate manifest-digests --repo .mybackup
```

Run once when upgrading repos that predate digest sidecars (trusted manifests only).

---

## Health checks

| Check | Command | Healthy signal |
|-------|---------|----------------|
| CLI | `backup-tool version` | prints `0.1.0`, exit 0 |
| Metadata | `info --repo PATH` | `sha256`, `content-addressable`, `sha256-prefix-2` |
| Snapshot | `verify latest --repo PATH` | exit 0, no missing/hash errors |
| Repository | `check --repo PATH` | exit 0; orphans may warn only |
| Restore spot-check | restore to empty dir | files match expectations |

---

## Failure modes and recovery

### Lock (exit 5)

```text
lock error: Repository is locked
```

1. Confirm no active `backup-tool` process.
2. Retry with `--break-lock` only if safe.
3. `backup-tool check --repo PATH`.

### Partial backup (exit 3)

```text
warning: snapshot is partial (N file(s) skipped)
```

Inspect `--verbose` output; fix permissions or concurrent writers; rerun. Use `--strict`
when partial snapshots are unacceptable.

### Missing blob (exit 2 on verify)

Restore object from another repo copy or use an older snapshot. Run `check` for scope.
**Do not run `gc` until recovery is decided.**

### Digest missing / mismatch

```powershell
backup-tool migrate manifest-digests --repo PATH   # missing sidecar
```

Mismatch: restore manifest + `.sha256` from known-good copy; then `check`.

### Orphan blobs after prune

```powershell
backup-tool gc --repo PATH
```

### Interrupted backup

```powershell
backup-tool check --repo PATH
backup-tool gc --repo PATH --aggressive   # if stale tmp reported
backup-tool backup ...
```

### Malformed object paths

```powershell
backup-tool check --repo PATH --repair
```

Quarantine: `tmp/quarantine/`.

---

## Troubleshooting tree

```text
Failed command
├── exit 5 → wait or --break-lock (no active process)
├── exit 2 → verify → check → recover blobs/manifests
├── exit 3 → partial backup / strict abort / partial restore symlinks
├── exit 1 → args, init, paths, excludes, destination policy
└── exit 4 → unexpected; reproduce minimally, inspect repo on disk
```

---

## Maintenance habits

- Keep the repository **outside** the source tree when possible.
- Never hand-edit manifests or delete objects under `objects/`.
- Run `verify latest` after meaningful backups; `prune --gc` after retention changes.
- Keep a **second copy** of the repository if data matters (tool is local, not cloud DR).
- Treat shared repos as untrusted unless using `--safe-symlinks` on restore.

---

## Development smoke test

```powershell
pytest
ruff check backup_tool tests
mypy backup_tool
coverage run -m pytest && coverage report -m
```

Windows: symlink tests need Developer Mode or equivalent privilege.
