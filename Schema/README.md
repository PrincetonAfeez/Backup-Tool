# Backup Tool Schema Files

This folder contains simple JSON Schema files for the academic Backup Tool project.

## Files

- `repo-metadata.schema.json` — validates `.mybackup/repo.json`.
- `manifest.schema.json` — validates snapshot manifest JSON files.
- `file-entry.schema.json` — validates a single manifest entry.
- `snapshot-summary.schema.json` — validates simple snapshot summary objects.
- `schema-index.json` — lists the schemas in this folder.

## Notes

These schemas target Backup Tool Version 1 and mirror the current project format:

- SHA-256 hashes are lowercase 64-character hex strings.
- Snapshot IDs use the project format `YYYY-MM-DDTHH-MM-SS-microsecondsZ_<8 hex chars>`.
- Manifest statuses are `complete`, `partial`, and `dry-run`.
- File entries may be `file`, `symlink`, or `directory`.
- Repository metadata uses `sha256`, `content-addressable`, `sha256-prefix-2`, and `fixed-1mb-blocks-above-threshold`.

These schemas are intentionally simple and academic. They are useful for documentation,
validation experiments, and explaining the repository format.
