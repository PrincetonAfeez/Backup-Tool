"""Simple repository lock file."""

from __future__ import annotations

import os
import time
from pathlib import Path
from secrets import token_hex
from types import TracebackType

from backup_tool.atomic import fsync_directory
from backup_tool.errors import LockError


ERROR_ACCESS_DENIED = 5


def read_lock_pid(path: Path) -> int | None:
    """Return the PID recorded in a lock file, if present."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    for line in text.splitlines():
        if line.startswith("pid="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                return None
    return None


def read_lock_token(path: Path) -> str | None:
    """Return the acquisition token recorded in a lock file, if present."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    for line in text.splitlines():
        if line.startswith("token="):
            token = line.split("=", 1)[1]
            return token or None
    return None


def _write_all(fd: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(fd, data[offset:])
        if written == 0:
            raise OSError("Failed to write lock payload")
        offset += written


def is_process_alive(pid: int) -> bool:
    """Return True when the given PID appears to be running."""

    if pid <= 0:
        return False

    if os.name == "nt":
        import ctypes

        process_query_limited = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        if ctypes.windll.kernel32.GetLastError() == ERROR_ACCESS_DENIED:
            return True
        return False

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to another user.
        return True
    except OSError:
        return False
    return True


def clear_stale_lock(path: Path) -> int | None:
    """Remove a lock file when its owning process is no longer alive.

    Returns the cleared PID, or None when no stale lock was removed.
    """

    if not path.exists():
        return None

    pid = read_lock_pid(path)
    if pid is None:
        try:
            if path.stat().st_size == 0:
                path.unlink(missing_ok=True)
        except OSError:
            return None
        return None

    if is_process_alive(pid):
        return None

    path.unlink(missing_ok=True)
    return pid


class RepositoryLock:
    """Exclusive lock implemented with atomic lock-file creation."""

    def __init__(self, path: Path, break_lock: bool = False):
        self.path = path
        self.break_lock = break_lock
        self._fd: int | None = None
        self._token: str | None = None
        self.cleared_stale_pid: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cleared_stale_pid = None

        if self.break_lock and self.path.exists():
            self.path.unlink(missing_ok=True)

        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self._fd = os.open(self.path, flags)
        except FileExistsError as first_exc:
            cleared_pid = clear_stale_lock(self.path)
            if cleared_pid is not None:
                self.cleared_stale_pid = cleared_pid
            try:
                self._fd = os.open(self.path, flags)
            except FileExistsError:
                if not self.path.exists():
                    try:
                        self._fd = os.open(self.path, flags)
                    except FileExistsError as exc:
                        raise LockError(f"Repository is locked: {self.path}") from exc
                else:
                    raise LockError(f"Repository is locked: {self.path}") from first_exc

        self._token = token_hex(16)
        payload = f"pid={os.getpid()}\ntime={time.time()}\ntoken={self._token}\n"
        try:
            self._commit_lock_payload(payload)
        except Exception:
            self._abort_acquire()
            raise

    def _commit_lock_payload(self, payload: str) -> None:
        if self._fd is None:
            raise LockError("Lock file descriptor is not open")

        data = payload.encode("utf-8")
        _write_all(self._fd, data)
        os.fsync(self._fd)
        fsync_directory(self.path.parent)

    def _abort_acquire(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self.path.unlink(missing_ok=True)
        self._token = None

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self._token is not None and self.path.exists():
            if read_lock_token(self.path) == self._token:
                self.path.unlink(missing_ok=True)
        self._token = None

    def __enter__(self) -> "RepositoryLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
