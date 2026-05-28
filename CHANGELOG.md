# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-05-28
### Added
- Content-addressable SHA-256 object store with whole-file and 1 MiB block dedup.
- Immutable JSON snapshot manifests with `.sha256` digest sidecars.
- CLI: init, backup, list, info, show, restore, diff, verify, check, prune, gc,
  migrate manifest-digests.
- Staging-based backup/restore, repository locking with stale-lock recovery,
  retention (prune) and blob garbage collection (gc).
