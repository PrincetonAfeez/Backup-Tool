"""Tests for backup_tool.lock."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from backup_tool.errors import LockError
from backup_tool.lock import ERROR_ACCESS_DENIED, RepositoryLock, clear_stale_lock, is_process_alive, read_lock_pid, read_lock_token


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


def test_is_process_alive_windows_access_denied_is_alive(monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows-specific behavior")

    class FakeKernel32:
        def OpenProcess(self, *_args):
            return 0

        def GetLastError(self):
            return ERROR_ACCESS_DENIED

        def CloseHandle(self, _handle):
            return 0

    monkeypatch.setattr("ctypes.windll.kernel32", FakeKernel32())
    assert is_process_alive(99999) is True


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
    with RepositoryLock(lock_path):
        assert lock_path.exists()
        assert read_lock_pid(lock_path) == os.getpid()
        assert read_lock_token(lock_path)
    assert not lock_path.exists()


def test_repository_lock_break_lock(tmp_path: Path):
    lock_path = tmp_path / "lock"
    lock_path.write_text(f"pid={os.getpid()}\ntime=0\n", encoding="utf-8")
    with RepositoryLock(lock_path, break_lock=True):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_repository_lock_write_failure_removes_partial_lock(tmp_path: Path, monkeypatch):
    lock_path = tmp_path / "lock"
    real_replace = os.replace

    def fail_replace(src, dst):
        if str(dst) == str(lock_path):
            raise OSError("simulated replace failure")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_replace)
    lock = RepositoryLock(lock_path)
    with pytest.raises(OSError, match="simulated replace failure"):
        lock.acquire()
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


def test_repository_lock_release_does_not_steal_replaced_lock(tmp_path: Path):
    lock_path = tmp_path / "lock"
    first = RepositoryLock(lock_path)
    first.acquire()
    try:
        if first._fd is not None:
            os.close(first._fd)
            first._fd = None
        lock_path.unlink(missing_ok=True)

        second = RepositoryLock(lock_path)
        second.acquire()
        try:
            first.release()
            assert lock_path.exists()
            assert read_lock_token(lock_path) == second._token
        finally:
            second.release()
    finally:
        if lock_path.exists():
            lock_path.unlink(missing_ok=True)


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
