# Backup Tool Schema Files

JSON Schema (draft 2020-12) reference for Backup Tool Version 1. These schemas mirror
**runtime validation** in `backup_tool` (load, `check`, and manifest parsing)—not a
stricter superset.

## Files

| File | Validates |
|------|-----------|
| `repo-metadata.schema.json` | `repo.json` |
| `manifest.schema.json` | Snapshot manifest JSON |
| `file-entry.schema.json` | One manifest entry (also inlined in manifest schema) |
| `snapshot-summary.schema.json` | List/info summary objects |
| `schema-index.json` | Schema catalog |

## Runtime alignment

| Field / rule | Schema | Runtime (`validate_repo_metadata`, `Manifest`) |
|--------------|--------|------------------------------------------------|
| `repo.json` required keys | `version`, `hash_algorithm`, `storage`, `object_layout`, `chunking` | Same fields enforced by `check` |
| `repo.json` `created_at` | Optional property; type string | Written at `init`; **not** validated by `check` |
| Manifest `source` | Non-empty string (`minLength: 1`) | Non-empty stripped string |
| Manifest `stats`, `skipped` | Required | Always emitted by `Manifest.to_dict()` |
| SHA-256 hashes | 64 lowercase hex | `object_store.validate_hash` |
| Snapshot id | Pattern in schema | `staging.validate_snapshot_id` |

## Format notes

- Snapshot IDs: `YYYY-MM-DDTHH-MM-SS-microsecondsZ_<8 hex>`
- Status: `complete`, `partial`, `dry-run`
- Entry types: `file`, `symlink`, `directory`
- Repository constants: `sha256`, `content-addressable`, `sha256-prefix-2`, `fixed-1mb-blocks-above-threshold`

Use these schemas for documentation, portfolio review, and optional external validation.
Command behavior is authoritative in [docs/IDS.md](../docs/IDS.md) and the implementation.

**Packaging:** schemas ship with the source repository only. They are not included in the
`backup-tool` wheel (`pyproject.toml` packages `backup_tool*` only). Editable installs and
git checkouts include this directory.
