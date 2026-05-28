"""argparse CLI for backup_tool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import NoReturn

from backup_tool import __version__
from backup_tool.diff import DiffResult
from backup_tool.errors import BackupToolError, IntegrityError, LockError, ManifestError
from backup_tool.paths import validate_exclude_pattern, validate_restore_file_path
from backup_tool.repository import Repository


class BackupToolArgumentParser(argparse.ArgumentParser):
    """Argument parser that maps usage errors to exit code 1."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> BackupToolArgumentParser:
    parser = BackupToolArgumentParser(prog="backup-tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("version", help="print the backup-tool version")

    init_parser = subparsers.add_parser("init", help="initialize a repository")
    init_parser.add_argument("--repo", required=True, type=Path)
    init_parser.add_argument(
        "--allow-nonempty",
        action="store_true",
        help="initialize even if the directory already contains files",
    )
    _add_break_lock(init_parser)

    backup_parser = subparsers.add_parser("backup", help="create a snapshot")
    backup_parser.add_argument("src", type=Path)
    backup_parser.add_argument("--repo", required=True, type=Path)
    backup_parser.add_argument("--exclude", action="append", default=[])
    backup_parser.add_argument("--dry-run", action="store_true")
    backup_parser.add_argument("--strict", action="store_true")
    backup_parser.add_argument("--verbose", action="store_true")
    _add_break_lock(backup_parser)

    list_parser = subparsers.add_parser("list", help="list snapshots")
    list_parser.add_argument("--repo", required=True, type=Path)

    info_parser = subparsers.add_parser("info", help="show repository metadata and counts")
    info_parser.add_argument("--repo", required=True, type=Path)

    show_parser = subparsers.add_parser("show", help="print a snapshot manifest as JSON")
    show_parser.add_argument("snapshot")
    show_parser.add_argument("--repo", required=True, type=Path)

    restore_parser = subparsers.add_parser("restore", help="restore a snapshot")
    restore_parser.add_argument("snapshot")
    restore_parser.add_argument("--repo", required=True, type=Path)
    restore_parser.add_argument("--to", required=True, type=Path)
    restore_parser.add_argument("--file")
    restore_parser.add_argument("--force", action="store_true")
    restore_parser.add_argument(
        "--safe-symlinks",
        action="store_true",
        help="reject absolute or parent-escaping symlink targets during restore",
    )
    _add_break_lock(restore_parser)

    diff_parser = subparsers.add_parser("diff", help="compare two snapshots")
    diff_parser.add_argument("snapshot_a")
    diff_parser.add_argument("snapshot_b")
    diff_parser.add_argument("--repo", required=True, type=Path)
    diff_parser.add_argument("--verbose", action="store_true")

    verify_parser = subparsers.add_parser(
        "verify",
        help="verify blob content for one snapshot (loads digest sidecar during manifest read)",
    )
    verify_parser.add_argument("snapshot")
    verify_parser.add_argument("--repo", required=True, type=Path)

    check_parser = subparsers.add_parser("check", help="check the whole repository")
    check_parser.add_argument("--repo", required=True, type=Path)
    check_parser.add_argument(
        "--repair",
        action="store_true",
        help=(
            "repair safe repository hygiene issues: migrate missing manifest digests, "
            "quarantine malformed object paths, quarantine unloadable snapshot manifests, "
            "remove orphan manifest digest sidecars, remove stale tmp artifacts, and "
            "remove stale orphan staging dirs"
        ),
    )
    _add_break_lock(check_parser)

    prune_parser = subparsers.add_parser("prune", help="remove old manifests")
    prune_parser.add_argument("--repo", required=True, type=Path)
    prune_parser.add_argument("--keep", required=True, type=int)
    prune_parser.add_argument("--dry-run", action="store_true")
    prune_parser.add_argument(
        "--gc",
        action="store_true",
        help="run garbage collection after pruning (uses the same dry-run mode)",
    )
    _add_break_lock(prune_parser)

    gc_parser = subparsers.add_parser("gc", help="delete unreferenced blobs")
    gc_parser.add_argument("--repo", required=True, type=Path)
    gc_parser.add_argument("--dry-run", action="store_true")
    gc_parser.add_argument(
        "--aggressive",
        action="store_true",
        help="quarantine malformed object paths before garbage collection",
    )
    _add_break_lock(gc_parser)

    migrate_parser = subparsers.add_parser(
        "migrate",
        help="one-time repository format migrations",
    )
    migrate_sub = migrate_parser.add_subparsers(dest="migrate_target", required=True)
    digest_parser = migrate_sub.add_parser(
        "manifest-digests",
        help="write missing .sha256 sidecars for legacy manifests",
    )
    digest_parser.add_argument("--repo", required=True, type=Path)
    _add_break_lock(digest_parser)

    return parser


def _add_break_lock(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--break-lock",
        action="store_true",
        help="force remove a repository lock left by a crashed process",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return int(exc.code) if isinstance(exc.code, int) else 1

    try:
        if args.command == "version":
            print(__version__)
            return 0

        if args.command == "init":
            Repository.init(
                args.repo,
                break_lock=args.break_lock,
                allow_nonempty=args.allow_nonempty,
            )
            print(f"Initialized repository: {args.repo}")
            return 0

        repo = Repository(args.repo)

        if args.command == "backup":
            for pattern in args.exclude:
                validate_exclude_pattern(pattern)
            backup_result = repo.backup(
                args.src,
                excludes=args.exclude,
                dry_run=args.dry_run,
                strict=args.strict,
                break_lock=args.break_lock,
            )
            if backup_result.manifest is None:
                print("Backup aborted; no snapshot committed.", file=sys.stderr)
                for item in backup_result.skipped:
                    print(f"skipped: {item.path}: {item.reason}", file=sys.stderr)
                return 3

            manifest = backup_result.manifest
            if args.verbose and backup_result.stale_lock_cleared_pid is not None:
                print(
                    f"warning: removed stale lock (pid={backup_result.stale_lock_cleared_pid})",
                    file=sys.stderr,
                )
            for warning in backup_result.warnings:
                print(f"warning: {warning}", file=sys.stderr)

            if backup_result.dry_run:
                print(
                    f"Dry run: snapshot {manifest.snapshot_id} was not committed.",
                    file=sys.stderr,
                )
            else:
                print(f"Snapshot {manifest.snapshot_id} committed.")
                if manifest.status == "partial":
                    skipped_count = manifest.stats.get(
                        "skipped_files",
                        len(backup_result.skipped),
                    )
                    print(
                        f"warning: snapshot is partial ({skipped_count} file(s) skipped)",
                        file=sys.stderr,
                    )

            _print_backup_summary(manifest.stats)

            if args.verbose and backup_result.diff is not None:
                _print_diff(backup_result.diff, show_unchanged=True)
                for skipped_item in backup_result.skipped:
                    print(f"skipped: {skipped_item.path}: {skipped_item.reason}")
            return 3 if backup_result.skipped else 0

        if args.command == "list":
            summaries = repo.list_snapshots()
            if not summaries:
                print("No snapshots.")
                return 0

            latest_id = summaries[-1].snapshot_id
            for summary in summaries:
                prefix = "* " if summary.snapshot_id == latest_id else "  "
                status = summary.status
                if status == "partial":
                    status = f"{status} [PARTIAL]"
                print(
                    f"{prefix}{summary.snapshot_id}  {summary.created_at}  "
                    f"{status}  entries={summary.entry_count}  "
                    f"new_bytes={summary.new_bytes_stored}  source={summary.source}"
                )
            return 0

        if args.command == "info":
            info = repo.repo_info()
            print(json.dumps(info.metadata, indent=2, sort_keys=True))
            last = info.last_backup_at or "none"
            print(
                f"snapshots={info.snapshot_count} objects={info.object_count} "
                f"last_backup={last}",
                file=sys.stderr,
            )
            return 0

        if args.command == "show":
            manifest = repo.show_snapshot(args.snapshot)
            print(
                f"snapshot={manifest.snapshot_id} status={manifest.status} "
                f"files={len(manifest.files)} skipped={len(manifest.skipped)}",
                file=sys.stderr,
            )
            print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
            return 0

        if args.command == "restore":
            restore_file = validate_restore_file_path(args.file)
            restore_result = repo.restore(
                args.snapshot,
                args.to,
                file_path=restore_file,
                force=args.force,
                safe_symlinks=args.safe_symlinks,
                break_lock=args.break_lock,
            )
            dir_label = (
                "directory"
                if restore_result.restored_directories == 1
                else "directories"
            )
            print(
                f"Restored {restore_result.restored_files} file(s), "
                f"{restore_result.restored_directories} {dir_label}, and "
                f"{restore_result.restored_symlinks} symlink(s) to {restore_result.destination}"
            )
            for warning in restore_result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            if restore_result.failed_symlinks:
                print(
                    f"warning: restore is partial ({restore_result.failed_symlinks} symlink(s) failed)",
                    file=sys.stderr,
                )
            return 3 if restore_result.failed_symlinks else 0

        if args.command == "diff":
            diff_result = repo.diff(args.snapshot_a, args.snapshot_b)
            _print_diff(diff_result, show_unchanged=args.verbose)
            return 0

        if args.command == "verify":
            verify_result = repo.verify(args.snapshot)
            for warning in verify_result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            if verify_result.ok:
                print(f"Snapshot {verify_result.snapshot_id} verified.")
                return 0
            for error in verify_result.errors:
                print(f"error: {error}", file=sys.stderr)
            return 2

        if args.command == "check":
            check_result = repo.check(repair=args.repair, break_lock=args.break_lock)
            print(
                f"snapshots={check_result.snapshot_count} objects={check_result.object_count} "
                f"referenced={check_result.referenced_object_count} orphans={check_result.orphan_object_count}"
            )
            for warning in check_result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            for quarantined_item in check_result.quarantined_malformed:
                print(f"quarantined: {quarantined_item}")
            for quarantined_manifest in check_result.quarantined_manifests:
                print(f"quarantined: {quarantined_manifest}")
            if check_result.repaired and not check_result.ok:
                print(
                    "warning: repository partially repaired; unresolved errors remain.",
                    file=sys.stderr,
                )
            if check_result.orphan_object_count > 0:
                print(
                    f"hint: run `backup-tool gc --repo {args.repo}` to remove unreferenced blobs.",
                    file=sys.stderr,
                )
            if check_result.ok:
                print("Repository check passed.")
                return 0
            for error in check_result.errors:
                print(f"error: {error}", file=sys.stderr)
            return 2

        if args.command == "prune":
            prune_result = repo.prune(
                args.keep,
                dry_run=args.dry_run,
                run_gc=args.gc,
                break_lock=args.break_lock,
            )
            prefix = "Would delete" if prune_result.dry_run else "Deleted"
            print(f"{prefix} {len(prune_result.deleted_snapshots)} snapshot(s).")
            for snapshot_id in prune_result.deleted_snapshots:
                print(snapshot_id)
            if prune_result.gc_result is not None:
                gc_prefix = "Would delete" if prune_result.gc_result.dry_run else "Deleted"
                print(
                    f"{gc_prefix} {len(prune_result.gc_result.deleted_blobs)} blob(s); "
                    f"bytes={prune_result.gc_result.bytes_deleted}."
                )
            elif prune_result.deleted_snapshots:
                print(
                    f"hint: run `backup-tool gc --repo {args.repo}` or use --gc to reclaim blob space.",
                    file=sys.stderr,
                )
            return 0

        if args.command == "gc":
            gc_result = repo.gc(
                dry_run=args.dry_run,
                aggressive=args.aggressive,
                break_lock=args.break_lock,
            )
            prefix = "Would delete" if gc_result.dry_run else "Deleted"
            print(f"{prefix} {len(gc_result.deleted_blobs)} blob(s); bytes={gc_result.bytes_deleted}.")
            if gc_result.removed_tmp_files:
                tmp_prefix = "Would remove" if gc_result.dry_run else "Removed"
                print(
                    f"{tmp_prefix} {len(gc_result.removed_tmp_files)} stale tmp file(s); "
                    f"bytes={gc_result.tmp_bytes_deleted}."
                )
            for quarantined_item in gc_result.quarantined_malformed:
                print(f"quarantined: {quarantined_item}")
            return 0

        if args.command == "migrate":
            if args.migrate_target == "manifest-digests":
                migrate_result = repo.migrate_manifest_digests(break_lock=args.break_lock)
                if migrate_result.migrated:
                    print(f"Migrated {len(migrate_result.migrated)} manifest digest sidecar(s).")
                    for snapshot_id in migrate_result.migrated:
                        print(snapshot_id)
                else:
                    print("No manifests required digest migration.")
                for skipped_message in migrate_result.skipped:
                    print(f"skipped: {skipped_message}", file=sys.stderr)
                return 0

        parser.error(f"Unknown command: {args.command}")
        return 1

    except IntegrityError as exc:
        print(f"integrity error: {exc}", file=sys.stderr)
        return 2
    except LockError as exc:
        print(f"lock error: {exc}", file=sys.stderr)
        return 5
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except BackupToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"internal error: {exc}", file=sys.stderr)
        return 4


def _print_backup_summary(stats: dict[str, int]) -> None:
    print(
        "Summary: "
        f"entries={stats.get('entry_count', 0)} "
        f"files={stats.get('regular_file_count', 0)} "
        f"new={stats.get('new_files', 0)} "
        f"changed={stats.get('changed_files', 0)} "
        f"deleted={stats.get('deleted_files', 0)} "
        f"unchanged={stats.get('unchanged_files', 0)} "
        f"scanned_bytes={stats.get('total_bytes_scanned', 0)} "
        f"new_bytes={stats.get('new_bytes_stored', 0)} "
        f"skipped={stats.get('skipped_files', 0)}"
    )


def _print_diff(result: DiffResult, show_unchanged: bool = False) -> None:
    added = sorted(result.added)
    changed = sorted(result.changed)
    deleted = sorted(result.deleted)
    unchanged = sorted(result.unchanged)
    print(f"Added ({len(added)}):")
    for path in added:
        print(f"  + {path}")
    print(f"Changed ({len(changed)}):")
    for path in changed:
        print(f"  ~ {path}")
    print(f"Deleted ({len(deleted)}):")
    for path in deleted:
        print(f"  - {path}")
    if show_unchanged:
        print(f"Unchanged ({len(unchanged)}):")
        for path in unchanged:
            print(f"    {path}")
    print(
        f"Summary: {len(added)} added, {len(changed)} changed, "
        f"{len(deleted)} deleted, {len(unchanged)} unchanged"
    )


if __name__ == "__main__":
    raise SystemExit(main())
