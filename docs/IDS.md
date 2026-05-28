# Interface Design Specification — Backup Tool

**Status:** Accepted · **Version:** 0.1.0

Public operator surface: `backup-tool <command> [options]` or `python -m backup_tool.cli`.
Library contracts: `Repository` and types in [TDD](TDD.md). JSON shapes: [`Schema/`](../Schema/).

---

## Commands

| Command / flag | Purpose |
|----------------|---------|
| `--version` | Print package version (no subcommand) |
| `version` | Print package version (subcommand) |
| `init` | Initialize repository |
| `backup` | Create snapshot |
| `list` | List snapshots |
| `info` | Repository metadata + counts |
| `show` | Snapshot manifest JSON |
| `restore` | Restore snapshot or subtree |
| `diff` | Compare two snapshots |
| `verify` | Verify one snapshot |
| `check` | Check full repository |
| `prune` | Delete old snapshot manifests |
| `gc` | Delete unreferenced blobs |
| `migrate manifest-digests` | Write missing digest sidecars |

---

## Invocation reference

### `--version` / `version`

```powershell
backup-tool --version
backup-tool version
```

`--version` stdout: `<version>` · `version` subcommand stdout: `<version>` · Exit: 0

### `init`

```powershell
backup-tool init --repo <path> [--allow-nonempty] [--break-lock]
```

| Flag | Required | Description |
|------|:--------:|-------------|
| `--repo` | yes | Repository directory |
| `--allow-nonempty` | no | Allow init in non-empty directory |
| `--break-lock` | no | Remove stale lock before init |

Success: `Initialized repository: <path>` · Exit: 0

### `backup`

```powershell
backup-tool backup <src> --repo <path> [--exclude <pattern>]... [--dry-run] [--strict] [--verbose] [--break-lock]
```

| Argument / flag | Required | Description |
|-----------------|:--------:|-------------|
| `<src>` | yes | Source directory |
| `--repo` | yes | Repository path |
| `--exclude` | no | Repeatable manifest-relative pattern |
| `--dry-run` | no | Build diff without commit |
| `--strict` | no | Abort if any file skipped |
| `--verbose` | no | Skipped paths, diff detail |
| `--break-lock` | no | Clear stale lock |

Success: `Snapshot <id> committed.` + summary line · Partial: warning + exit 3 · Strict abort: exit 3, no commit

### `list`

```powershell
backup-tool list --repo <path>
```

Stdout: one line per snapshot; `*` = newest; `[PARTIAL]` when skipped files recorded

### `info`

```powershell
backup-tool info --repo <path>
```

Stdout: `repo.json` metadata JSON · Stderr: `snapshots=N objects=M last_backup=...`

### `show`

```powershell
backup-tool show <snapshot> --repo <path>
```

`<snapshot>`: exact id, `<id>.json`, or `latest`. Stderr: one-line summary · Stdout: full manifest JSON (`Manifest.to_dict()`)

### `restore`

```powershell
backup-tool restore <snapshot> --repo <path> --to <dest> [--file <rel-path>] [--force] [--safe-symlinks] [--break-lock]
```

Stages under `.restore-<snapshot-id>.*` beside `destination.parent`.

- **Full restore** (no `--file`): atomically replaces `--to` after staging (requires empty
  destination or `--force`).
- **Spot restore** (`--file`): merges selected manifest paths into `--to`; unrelated files
  in an existing destination are preserved.

See [TDD restore flow](TDD.md#restore).

### `verify` vs `check`

| Concern | `verify` | `check` |
|---------|----------|---------|
| Manifest load + digest sidecar | Selected snapshot (via `ManifestStore.load`) | All snapshots |
| Blob presence and file hash | Selected snapshot | All snapshots |
| Manifest stats consistency | No | Yes |
| Orphan blobs / hygiene | No | Warns; `--repair` fixes safe issues |

`verify` loads and digest-checks the selected manifest, then verifies referenced blobs.
It does not detect malicious tampering when manifest and sidecar are rewritten together.
`check` validates all manifests, stats, references, and repository hygiene.
See [ADR 0009](adr/0009-manifest-trust-and-tamper-model.md).

### `diff`

```powershell
backup-tool diff <snapshot-a> <snapshot-b> --repo <path> [--verbose]
```

Stdout: Added / Changed / Deleted groups + summary

### `verify`

```powershell
backup-tool verify <snapshot> --repo <path>
```

Success: `Snapshot <id> verified.` · Unknown/invalid snapshot: exit 1 (same as `show`/`restore`/`diff`) · Blob integrity failure: exit 2 (`error: <path>: ...`)
Loads the manifest digest sidecar during manifest read; does not validate stats consistency.

### `check`

```powershell
backup-tool check --repo <path> [--repair] [--break-lock]
```

Stdout: counts + `Repository check passed.` or errors · `--repair`: migrate missing manifest
digests, quarantine malformed object paths, quarantine unloadable snapshot manifests,
remove orphan digest sidecars, remove stale tmp artifacts, remove orphan `tmp/staging/` dirs.

### `prune`

```powershell
backup-tool prune --repo <path> --keep N [--dry-run] [--gc] [--break-lock]
```

Deletes oldest manifests beyond `N`; `--gc` runs blob GC in same lock scope

### `gc`

```powershell
backup-tool gc --repo <path> [--dry-run] [--aggressive] [--break-lock]
```

`--aggressive`: also quarantine malformed object paths and remove stale tmp artifacts

### `migrate manifest-digests`

```powershell
backup-tool migrate manifest-digests --repo <path> [--break-lock]
```

---

## Exit codes

| Code | Meaning |
|:----:|---------|
| 0 | Success |
| 1 | General / repository / argument error |
| 2 | Integrity failure (`verify` blob checks, `check`) |
| 3 | Partial backup, strict abort, or partial restore |
| 4 | Unexpected internal error |
| 5 | Lock not acquired |

---

## Input contracts

### Source directory (`backup`)

- Must exist and be a directory.
- Must not equal the repository or lie inside it.
- If the repository is inside the source tree, that path is auto-excluded with a warning.

### Manifest paths

POSIX-style relative paths: no leading `/`, no `..`, no empty components. Validated at backup and restore.

### Exclude patterns

Normalized to `/`; `..` rejected; bare `*` and `**` rejected. See [README exclude table](../README.md#exclude-patterns).

### Symlink targets (`restore --safe-symlinks`)

Rejects empty, absolute, drive-letter, UNC, and `..`-escaping targets.

---

## Output contracts

### `repo.json`

Fields written at init: `version`, `created_at`, `hash_algorithm`, `storage`, `object_layout`, `chunking`.
`check` validates version, hash algorithm, storage, object layout, and chunking (not `created_at` format).

### Manifest JSON

Always includes `version`, `snapshot_id`, `created_at`, `source` (non-empty string), `hash_algorithm`,
`status`, `stats`, `files`, `skipped`. Schema: [`manifest.schema.json`](../Schema/manifest.schema.json).

### Blob path

```text
objects/<first-two-hash-chars>/<full-sha256>
```

---

## Environment variables

None required at runtime. Dev/CI use `pyproject.toml` tool sections (pytest, coverage, ruff, mypy).

---

## Side effects

| Operation | Side effects |
|-----------|--------------|
| `init` | Creates repo dirs, writes `repo.json`, uses lock |
| `backup` | Reads source; stages under `tmp/staging/`; may promote objects; writes manifest + sidecar |
| `backup --dry-run` | Reads/hashes; no commit |
| `restore` | Writes `.restore-*` beside destination; replaces `--to` when allowed |
| `verify` / `check` | Read-only except `--repair` hygiene writes |
| `check --repair` | Quarantine malformed objects; delete orphan sidecars and staging dirs |
| `prune` | Deletes manifest + sidecar files |
| `gc` | Deletes unreferenced blobs; aggressive mode may quarantine + clean tmp |
| `migrate manifest-digests` | Writes missing `.sha256` sidecars |
| `--break-lock` | Unlinks `lock` before acquire |

---

## Public Python API

Primary entry point: `Repository` (exported from `backup_tool`).

Additional types (`Manifest`, `FileEntry`, `ObjectStore`, `SnapshotEngine`, result
dataclasses) are public by submodule import, for example `from backup_tool.manifest import Manifest`.
The package `__init__` intentionally exports only `Repository` to keep the facade narrow.

---

## Related documents

- [README](../README.md) — quick start and safety rules
- [TDD](TDD.md) — internal flows and modules
- [Runbook](RUNBOOK.md) — operations and recovery
- [Schema/README.md](../Schema/README.md) — JSON reference schemas
