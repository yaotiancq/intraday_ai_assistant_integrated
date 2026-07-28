from __future__ import annotations

"""Advisory, process-safe locks for logical market-analysis jobs."""

from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path
import socket
from typing import IO


class LockUnavailable(RuntimeError):
    """Raised when another process currently owns a non-blocking job lock."""


@dataclass(frozen=True)
class LockOwner:
    pid: int
    host: str
    acquired_at: str

    def to_dict(self) -> dict[str, str | int]:
        return {
            "pid": self.pid,
            "host": self.host,
            "acquired_at": self.acquired_at,
        }


class JobLock:
    """A ``flock``-backed lock that is automatically released after a crash.

    The lock file may remain on disk, but ownership is held by the open file
    descriptor and therefore cannot become permanently stale.  This is safer
    than using only ``O_EXCL`` marker files for a long-running scheduler.
    """

    def __init__(self, path: str | Path, *, blocking: bool = False) -> None:
        self.path = Path(path)
        self.blocking = blocking
        self._handle: IO[str] | None = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self, *, blocking: bool | None = None) -> bool:
        if self.acquired:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        should_block = self.blocking if blocking is None else blocking
        try:
            _lock_file(handle, blocking=should_block)
        except BlockingIOError:
            handle.close()
            return False

        owner = LockOwner(
            pid=os.getpid(),
            host=socket.gethostname(),
            acquired_at=datetime.now(timezone.utc).isoformat(),
        )
        handle.seek(0)
        handle.truncate()
        json.dump(owner.to_dict(), handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
        self._handle = handle
        return True

    def acquire_or_raise(self, *, blocking: bool | None = None) -> "JobLock":
        if not self.acquire(blocking=blocking):
            raise LockUnavailable(f"job is already running: {self.path}")
        return self

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock_file(handle)
        finally:
            handle.close()

    def __enter__(self) -> "JobLock":
        return self.acquire_or_raise()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def _lock_file(handle: IO[str], *, blocking: bool) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows compatibility path
        _portable_exclusive_lock(handle)
        return
    flags = fcntl.LOCK_EX
    if not blocking:
        flags |= fcntl.LOCK_NB
    try:
        fcntl.flock(handle.fileno(), flags)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            raise BlockingIOError from exc
        raise


def _unlock_file(handle: IO[str]) -> None:
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows compatibility path
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _portable_exclusive_lock(handle: IO[str]) -> None:
    """Best effort fallback for platforms without ``fcntl``.

    Production containers are Linux; this path exists so repository utilities
    remain importable on Windows development hosts.
    """

    try:  # pragma: no cover - exercised only on Windows
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:  # pragma: no cover
        raise BlockingIOError from exc


def lock_filename(run_key: str) -> str:
    """Return a filesystem-safe, collision-free-enough name for *run_key*."""

    import hashlib

    readable = "".join(character if character.isalnum() or character in "-_" else "_" for character in run_key)
    digest = hashlib.sha256(run_key.encode("utf-8")).hexdigest()[:12]
    return f"{readable[:80]}-{digest}.lock"
