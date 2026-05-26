"""Tests for backup_tool.lock."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from backup_tool.errors import LockError
from backup_tool.lock import RepositoryLock, clear_stale_lock, is_process_alive, read_lock_pid


def test_read_lock_pid_parses_pid(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_text("pid=42\ntime=0\n", encoding="utf-8")
    assert read_lock_pid(lock_path) == 42


def test_read_lock_pid_invalid_content(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_text("pid=not-a-number\n", encoding="utf-8")
    assert read_lock_pid(lock_path) is None


def test_read_lock_pid_missing_file(tmp_path: Path):
    assert read_lock_pid(tmp_path / "missing") is None


def test_is_process_alive_false_for_invalid_pids():
    assert is_process_alive(0) is False
    assert is_process_alive(-1) is False


def test_clear_stale_lock_removes_dead_pid(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_text("pid=0\ntime=0\n", encoding="utf-8")
    assert clear_stale_lock(lock_path) == 0
    assert not lock_path.exists()


def test_clear_stale_lock_keeps_live_pid(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / "lock"
    lock_path.write_text(f"pid={os.getpid()}\ntime=0\n", encoding="utf-8")
    monkeypatch.setattr("backup_tool.lock.is_process_alive", lambda pid: True)
    assert clear_stale_lock(lock_path) is None
    assert lock_path.exists()


def test_repository_lock_context_manager(tmp_path: Path):
    lock_path = tmp_path / "lock"
    with RepositoryLock(lock_path) as lock:
        assert lock_path.exists()
        assert read_lock_pid(lock_path) == os.getpid()
    assert not lock_path.exists()


def test_repository_lock_break_lock(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_text(f"pid={os.getpid()}\ntime=0\n", encoding="utf-8")
    with RepositoryLock(lock_path, break_lock=True):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_repository_lock_raises_when_active(tmp_path: Path):
    lock_path = tmp_path / "lock"
    first = RepositoryLock(lock_path)
    first.acquire()
    try:
        with pytest.raises(LockError, match="Repository is locked"):
            RepositoryLock(lock_path).acquire()
    finally:
        first.release()


def test_repository_lock_auto_clears_stale_pid(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_text("pid=0\ntime=0\n", encoding="utf-8")
    with RepositoryLock(lock_path) as lock:
        assert lock.cleared_stale_pid == 0


def test_concurrent_second_lock_raises(tmp_path: Path):
    lock_path = tmp_path / "lock"
    ready = threading.Event()
    release = threading.Event()
    caught: list[Exception] = []

    def holder():
        with RepositoryLock(lock_path):
            ready.set()
            release.wait(timeout=5)

    def challenger():
        try:
            RepositoryLock(lock_path).acquire()
        except Exception as exc:
            caught.append(exc)

    thread = threading.Thread(target=holder)
    thread.start()
    assert ready.wait(timeout=5)
    challenger_thread = threading.Thread(target=challenger)
    challenger_thread.start()
    challenger_thread.join(timeout=5)
    release.set()
    thread.join(timeout=5)
    assert len(caught) == 1
    assert isinstance(caught[0], LockError)
