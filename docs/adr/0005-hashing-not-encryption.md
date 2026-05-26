# ADR 0005: Hashing Is Not Encryption

## Decision

Use SHA-256 for addressing, deduplication, and integrity only.

## Reason

This project teaches content-addressed persistence, not confidentiality.

## Trade-off

Blob contents are readable on disk. Encryption is future work.
