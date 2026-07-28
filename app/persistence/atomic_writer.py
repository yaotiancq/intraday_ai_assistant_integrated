from __future__ import annotations

"""Small, dependency-free helpers for crash-safe file replacement.

The temporary file is created beside the destination so ``os.replace`` is an
atomic operation on the same filesystem.  Flushing both the file and (where
supported) its parent directory makes the write durable across process or host
restarts, rather than merely atomic to concurrent readers.
"""

import json
import os
from pathlib import Path
import tempfile
from typing import Any


def atomic_write_bytes(path: str | Path, content: bytes, *, mode: int = 0o644) -> Path:
    """Atomically replace *path* with *content* and return the destination."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(destination.parent),
        )
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, destination)
            temporary_name = None
            _fsync_directory(destination.parent)
        except BaseException:
            # ``fdopen`` owns the descriptor after it is constructed.  If an
            # error happened before that point, closing an already-closed fd is
            # harmlessly ignored here.
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return destination


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int = 0o644,
) -> Path:
    """Atomically write text without exposing a partially-written file."""

    return atomic_write_bytes(path, content.encode(encoding), mode=mode)


def atomic_write_json(
    path: str | Path,
    value: Any,
    *,
    indent: int | None = 2,
    sort_keys: bool = True,
    mode: int = 0o644,
) -> Path:
    """Serialize JSON deterministically and atomically replace *path*."""

    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=indent,
        sort_keys=sort_keys,
        separators=(",", ":") if indent is None else None,
    )
    return atomic_write_text(path, serialized + "\n", mode=mode)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory flush (not supported on every platform)."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


# Friendly aliases used by older callers and tests.
write_bytes_atomic = atomic_write_bytes
write_text_atomic = atomic_write_text
write_json_atomic = atomic_write_json
