"""Tests for backup_tool.cli."""

from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch


from backup_tool import __version__
from backup_tool.cli import _print_backup_summary, _print_diff, build_parser, main
from backup_tool.diff import DiffResult
from backup_tool.errors import IntegrityError, LockError
from backup_tool.repository import Repository
from tests.conftest import skip_skip_me


def test_build_parser_parses_subcommands():
    parser = build_parser()
    assert parser.parse_args(["version"]).command == "version"
    assert parser.parse_args(["init", "--repo", "repo"]).command == "init"
    assert parser.parse_args(["check", "--repo", "repo"]).command == "check"


def test_cli_version():
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert main(["version"]) == 0
    assert stdout.getvalue().strip() == __version__


def test_cli_init_and_smoke(repo_path: Path, source_dir: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    restore = tmp_path / "restore"
    assert main(["init", "--repo", str(repo_path)]) == 0
    assert main(["backup", str(source_dir), "--repo", str(repo_path)]) == 0
    assert main(["list", "--repo", str(repo_path)]) == 0
    assert main(["info", "--repo", str(repo_path)]) == 0
    assert main(["show", "latest", "--repo", str(repo_path)]) == 0
    assert main(["verify", "latest", "--repo", str(repo_path)]) == 0
    assert main(["restore", "latest", "--repo", str(repo_path), "--to", str(restore)]) == 0
    assert (restore / "a.txt").read_text(encoding="utf-8") == "hello"


def test_cli_list_empty_repo(repo: Repository, repo_path: Path, capsys):
    assert main(["list", "--repo", str(repo_path)]) == 0
    assert "No snapshots." in capsys.readouterr().out


def test_cli_list_marks_latest(repo: Repository, source_dir: Path, repo_path: Path):
    (source_dir / "a.txt").write_text("one", encoding="utf-8")
    main(["backup", str(source_dir), "--repo", str(repo_path)])
    (source_dir / "b.txt").write_text("two", encoding="utf-8")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        main(["backup", str(source_dir), "--repo", str(repo_path)])
        main(["list", "--repo", str(repo_path)])
    lines = stdout.getvalue().splitlines()
    assert lines[-2].startswith("  ")
    assert lines[-1].startswith("* ")


def test_cli_strict_aborts(monkeypatch, repo_path: Path, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    main(["init", "--repo", str(repo_path)])
    original = Repository.backup

    def backup_with_skip(self, source, skip_predicate=None, **kwargs):
        if skip_predicate is None:
            skip_predicate = skip_skip_me
        return original(self, source, skip_predicate=skip_predicate, **kwargs)

    monkeypatch.setattr(Repository, "backup", backup_with_skip)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["backup", str(source_dir), "--repo", str(repo_path), "--strict"])
    assert code == 3
    assert "Backup aborted" in stderr.getvalue()


def test_cli_partial_backup_warning(monkeypatch, repo_path: Path, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    main(["init", "--repo", str(repo_path)])
    original = Repository.backup

    def backup_with_skip(self, source, skip_predicate=None, **kwargs):
        if skip_predicate is None:
            skip_predicate = skip_skip_me
        return original(self, source, skip_predicate=skip_predicate, **kwargs)

    monkeypatch.setattr(Repository, "backup", backup_with_skip)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["backup", str(source_dir), "--repo", str(repo_path)])
    assert code == 3
    assert "snapshot is partial" in stderr.getvalue()


def test_cli_break_lock(repo_path: Path, source_dir: Path):
    (source_dir / "data.txt").write_text("data", encoding="utf-8")
    main(["init", "--repo", str(repo_path)])
    (repo_path / "lock").write_text(f"pid={os.getpid()}\ntime=0\n", encoding="utf-8")
    assert main(["backup", str(source_dir), "--repo", str(repo_path), "--break-lock"]) == 0


def test_cli_diff_and_verbose_backup(repo: Repository, source_dir: Path, repo_path: Path):
    (source_dir / "a.txt").write_text("one", encoding="utf-8")
    main(["backup", str(source_dir), "--repo", str(repo_path)])
    (source_dir / "b.txt").write_text("two", encoding="utf-8")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        main(["backup", str(source_dir), "--repo", str(repo_path), "--verbose"])
        snapshots = Repository(repo_path).list_snapshots()
        main(["diff", snapshots[0].snapshot_id, snapshots[1].snapshot_id, "--repo", str(repo_path), "--verbose"])
    output = stdout.getvalue()
    assert "Added (" in output
    assert "Unchanged (" in output


def test_cli_prune_hint_and_gc(repo: Repository, source_dir: Path, repo_path: Path):
    (source_dir / "a.txt").write_text("one", encoding="utf-8")
    repo.backup(source_dir)
    (source_dir / "a.txt").write_text("two", encoding="utf-8")
    repo.backup(source_dir)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        main(["prune", "--repo", str(repo_path), "--keep", "1"])
    assert "hint:" in stderr.getvalue()


def test_cli_check_orphan_hint(repo: Repository, source_dir: Path, repo_path: Path):
    (source_dir / "a.txt").write_text("one", encoding="utf-8")
    repo.backup(source_dir)
    (source_dir / "a.txt").write_text("two", encoding="utf-8")
    repo.backup(source_dir)
    repo.prune(keep=1)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        main(["check", "--repo", str(repo_path)])
    assert "hint:" in stderr.getvalue()


def test_cli_verify_failure(repo: Repository, source_dir: Path, repo_path: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    entry = next(iter(repo.manifest_store.latest().files.values()))
    blob_hash = entry.chunks[0] if entry.chunks else entry.hash
    repo.object_store.get_path(blob_hash).write_text("bad", encoding="utf-8")
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["verify", "latest", "--repo", str(repo_path)])
    assert code == 2


def test_cli_gc_dry_run(repo: Repository, repo_path: Path):
    orphan = repo.object_store.put_bytes(b"orphan-bytes")
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(["gc", "--repo", str(repo_path), "--dry-run"])
    assert code == 0
    output = stdout.getvalue()
    assert "Would delete" in output
    assert f"bytes={len(b'orphan-bytes')}" in output
    assert repo.object_store.exists(orphan.hash_hex)


def test_cli_dry_run_backup_with_skipped_returns_three(monkeypatch, repo_path: Path, source_dir: Path):
    (source_dir / "keep.txt").write_text("keep", encoding="utf-8")
    (source_dir / "skip-me.txt").write_text("skip", encoding="utf-8")
    main(["init", "--repo", str(repo_path)])
    original = Repository.backup

    def backup_with_skip(self, source, skip_predicate=None, **kwargs):
        if skip_predicate is None:
            skip_predicate = skip_skip_me
        return original(self, source, skip_predicate=skip_predicate, **kwargs)

    monkeypatch.setattr(Repository, "backup", backup_with_skip)
    assert main(["backup", str(source_dir), "--repo", str(repo_path), "--dry-run"]) == 3


def test_print_helpers(capsys):
    _print_backup_summary({"entry_count": 1, "regular_file_count": 1, "new_files": 1})
    _print_diff(DiffResult(["a"], [], [], ["b"]), show_unchanged=True)
    output = capsys.readouterr().out
    assert "Summary:" in output
    assert "Unchanged" in output


def test_cli_integrity_error_exit_code(repo_with_snapshot: Repository, repo_path: Path, monkeypatch):
    def fail_verify(self, snapshot_id):
        raise IntegrityError("bad")

    monkeypatch.setattr(Repository, "verify", fail_verify)
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["verify", "latest", "--repo", str(repo_path)])
    assert code == 2
    assert "integrity error" in stderr.getvalue()


def test_cli_lock_error_exit_code(repo: Repository, source_dir: Path, repo_path: Path):
    stderr = io.StringIO()
    with patch("backup_tool.cli.Repository.backup", side_effect=LockError("locked")):
        with redirect_stderr(stderr):
            code = main(["backup", str(source_dir), "--repo", str(repo_path)])
    assert code == 5


def test_cli_migrate_manifest_digests(repo: Repository, source_dir: Path, repo_path: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    repo.backup(source_dir)
    path = repo.manifest_store.path_for(repo.manifest_store.latest().snapshot_id)
    sidecar = path.with_name(f"{path.name}.sha256")
    sidecar.unlink()

    code = main(["migrate", "manifest-digests", "--repo", str(repo_path)])
    assert code == 0
    assert sidecar.exists()


def test_cli_repository_error_exit_code(repo_path: Path):
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["backup", str(repo_path), "--repo", str(repo_path)])
    assert code == 1


def test_cli_invalid_arguments_exit_code_one():
    assert main(["backup"]) == 1


def test_cli_info_counts_on_stderr(repo: Repository, repo_path: Path):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        assert main(["info", "--repo", str(repo_path)]) == 0
    assert stdout.getvalue().strip().startswith("{")
    assert stderr.getvalue().strip().startswith("snapshots=")


def test_cli_internal_error_exit_code(repo: Repository, source_dir: Path, repo_path: Path):
    with patch("backup_tool.cli.Repository.backup", side_effect=RuntimeError("boom")):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["backup", str(source_dir), "--repo", str(repo_path)])
    assert code == 4


def test_cli_restore_invalid_file_value(repo: Repository, source_dir: Path, repo_path: Path, tmp_path: Path):
    (source_dir / "a.txt").write_text("hello", encoding="utf-8")
    main(["backup", str(source_dir), "--repo", str(repo_path)])
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(
            [
                "restore",
                "latest",
                "--repo",
                str(repo_path),
                "--to",
                str(tmp_path / "restore"),
                "--file",
                "",
            ]
        )
    assert code == 1
    assert "Invalid --file value" in stderr.getvalue()


def test_cli_backup_rejects_bare_exclude_wildcard(repo_path: Path, source_dir: Path):
    main(["init", "--repo", str(repo_path)])
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["backup", str(source_dir), "--repo", str(repo_path), "--exclude", "*"])
    assert code == 1
    assert "Exclude pattern cannot be" in stderr.getvalue()
