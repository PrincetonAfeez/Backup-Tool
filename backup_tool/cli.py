"""argparse CLI for backup_tool."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from backup_tool import __version__
from backup_tool.errors import BackupToolError, IntegrityError, LockError, ManifestError
from backup_tool.paths import validate_exclude_pattern
from backup_tool.repository import Repository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="backup-tool")
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

    verify_parser = subparsers.add_parser("verify", help="verify a snapshot")
    verify_parser.add_argument("snapshot")
    verify_parser.add_argument("--repo", required=True, type=Path)

    check_parser = subparsers.add_parser("check", help="check the whole repository")
    check_parser.add_argument("--repo", required=True, type=Path)
    check_parser.add_argument(
        "--repair",
        action="store_true",
        help="quarantine malformed object paths into tmp/quarantine/",
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

    return parser


def _add_break_lock(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--break-lock",
        action="store_true",
        help="force remove a repository lock left by a crashed process",
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
            result = repo.backup(
                args.src,
                excludes=args.exclude,
                dry_run=args.dry_run,
                strict=args.strict,
                break_lock=args.break_lock,
            )
            if result.manifest is None:
                print("Backup aborted; no snapshot committed.", file=sys.stderr)
                for item in result.skipped:
                    print(f"skipped: {item.path}: {item.reason}", file=sys.stderr)
                return 3

            manifest = result.manifest
            if args.verbose and result.stale_lock_cleared_pid is not None:
                print(
                    f"warning: removed stale lock (pid={result.stale_lock_cleared_pid})",
                    file=sys.stderr,
                )
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)

            if result.dry_run:
                print(f"Dry run: snapshot {manifest.snapshot_id} was not committed.")
            else:
                print(f"Snapshot {manifest.snapshot_id} committed.")
                if manifest.status == "partial":
                    skipped_count = manifest.stats.get("skipped_files", len(result.skipped))
                    print(
                        f"warning: snapshot is partial ({skipped_count} file(s) skipped)",
                        file=sys.stderr,
                    )

            _print_backup_summary(manifest.stats)

            if args.verbose:
                _print_diff(result.diff, show_unchanged=True)
                for item in result.skipped:
                    print(f"skipped: {item.path}: {item.reason}")
            return 3 if result.skipped and not result.dry_run else 0

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
                    f"{status}  files={summary.file_count}  "
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
                f"files={len(manifest.files)} skipped={len(manifest.skipped)}"
            )
            print(json.dumps(manifest.to_dict(), indent=2, sort_keys=True))
            return 0

        if args.command == "restore":
            result = repo.restore(
                args.snapshot,
                args.to,
                file_path=args.file,
                force=args.force,
                safe_symlinks=args.safe_symlinks,
                break_lock=args.break_lock,
            )
            print(
                f"Restored {result.restored_files} file(s) and "
                f"{result.restored_symlinks} symlink(s) to {result.destination}"
            )
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            return 0

        if args.command == "diff":
            result = repo.diff(args.snapshot_a, args.snapshot_b)
            _print_diff(result, show_unchanged=args.verbose)
            return 0

        if args.command == "verify":
            result = repo.verify(args.snapshot)
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            if result.ok:
                print(f"Snapshot {result.snapshot_id} verified.")
                return 0
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
            return 2

        if args.command == "check":
            result = repo.check(repair=args.repair, break_lock=args.break_lock)
            print(
                f"snapshots={result.snapshot_count} objects={result.object_count} "
                f"referenced={result.referenced_object_count} orphans={result.orphan_object_count}"
            )
            for warning in result.warnings:
                print(f"warning: {warning}", file=sys.stderr)
            for item in result.quarantined_malformed:
                print(f"quarantined: {item}")
            if result.orphan_object_count > 0:
                print(
                    f"hint: run `backup-tool gc --repo {args.repo}` to remove unreferenced blobs.",
                    file=sys.stderr,
                )
            if result.ok:
                print("Repository check passed.")
                return 0
            for error in result.errors:
                print(f"error: {error}", file=sys.stderr)
            return 2

        if args.command == "prune":
            result = repo.prune(
                args.keep,
                dry_run=args.dry_run,
                run_gc=args.gc,
                break_lock=args.break_lock,
            )
            prefix = "Would delete" if result.dry_run else "Deleted"
            print(f"{prefix} {len(result.deleted_snapshots)} snapshot(s).")
            for snapshot_id in result.deleted_snapshots:
                print(snapshot_id)
            if result.gc_result is not None:
                gc_prefix = "Would delete" if result.gc_result.dry_run else "Deleted"
                print(
                    f"{gc_prefix} {len(result.gc_result.deleted_blobs)} blob(s); "
                    f"bytes={result.gc_result.bytes_deleted}."
                )
            elif result.deleted_snapshots:
                print(
                    f"hint: run `backup-tool gc --repo {args.repo}` or use --gc to reclaim blob space.",
                    file=sys.stderr,
                )
            return 0

        if args.command == "gc":
            result = repo.gc(
                dry_run=args.dry_run,
                aggressive=args.aggressive,
                break_lock=args.break_lock,
            )
            prefix = "Would delete" if result.dry_run else "Deleted"
            print(f"{prefix} {len(result.deleted_blobs)} blob(s); bytes={result.bytes_deleted}.")
            if result.removed_tmp_files:
                tmp_prefix = "Would remove" if result.dry_run else "Removed"
                print(
                    f"{tmp_prefix} {len(result.removed_tmp_files)} stale tmp file(s); "
                    f"bytes={result.tmp_bytes_deleted}."
                )
            for item in result.quarantined_malformed:
                print(f"quarantined: {item}")
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
        f"files={stats.get('file_count', 0)} "
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
