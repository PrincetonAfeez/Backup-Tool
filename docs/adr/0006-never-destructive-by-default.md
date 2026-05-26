# ADR 0006: Never Destructive by Default

## Decision

Backup and restore do not delete or overwrite user data by default.

## Reason

A backup tool's first responsibility is to avoid data loss.

## Trade-off

Users must opt into overwrite behavior with `--force`, and orphan blobs remain
until garbage collection runs.
