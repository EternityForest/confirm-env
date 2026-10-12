"""Durable, atomic text writes shared by the sidecar and providers files.

Both files are small pieces of user state that must never be left half
written if the device loses power mid-save, so they share one
implementation rather than each rolling their own.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import suppress
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write ``text`` to ``path``.

    Writes go to a temporary file in the same directory, are flushed and
    fsynced, then renamed over the target. This keeps the target intact if
    the device is unplugged or loses power mid-write.
    """
    if not path.parent.is_dir():
        raise FileNotFoundError(f"directory does not exist: {path.parent}")
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
        fsync_directory(path.parent)
    except BaseException:
        with suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def fsync_directory(directory: Path) -> None:
    """Flush a directory entry so a rename survives a power loss."""
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
