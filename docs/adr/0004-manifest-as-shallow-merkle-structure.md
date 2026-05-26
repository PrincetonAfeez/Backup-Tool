# ADR 0004: Manifest as a Shallow Merkle Structure

## Decision

Each snapshot manifest maps relative paths to content hashes.

## Reason

The manifest points to hashed content, giving the project the same basic shape
as Git object references without adding recursive tree objects.

## Trade-off

Directory tree objects are not modeled separately in Version 1.
