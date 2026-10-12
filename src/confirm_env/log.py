"""The run log: which commands ran against a store, and from where.

Every command run through confirm-env appends one row to the store's
``log`` table, giving a small audit trail that travels with the encrypted
database. The log is bounded: once it grows past :data:`MAX_ENTRIES` the
oldest :data:`TRIM_ENTRIES` rows are dropped, because the log lives inside
the store and must not grow without limit.
"""

from __future__ import annotations

import getpass
import shlex
import socket
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Newest entries kept before the oldest are trimmed.
MAX_ENTRIES = 1000

#: How many of the oldest entries are dropped when trimming.
TRIM_ENTRIES = 100

#: How many entries the editor shows per page.
PAGE_SIZE = 50


@dataclass(frozen=True)
class LogEntry:
    """One logged command run, as stored in the ``log`` table."""

    timestamp: str
    command: str
    username: str
    workdir: str
    hostname: str


def capture(command: Sequence[str], *, workdir: Path | None = None) -> LogEntry:
    """Build a :class:`LogEntry` describing a command about to be run.

    ``command`` is joined into a single shell-quoted line so the stored
    log reads like the line the user typed. The working directory defaults
    to the process's current directory.
    """
    return LogEntry(
        timestamp=_timestamp(),
        command=shlex.join(command),
        username=_username(),
        workdir=str(Path.cwd() if workdir is None else workdir),
        hostname=_hostname(),
    )


def _timestamp() -> str:
    """The current local time as an ISO 8601 string with its UTC offset."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _username() -> str:
    """The current user, or ``"unknown"`` when it cannot be determined."""
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        # A missing user must not stop a command from being logged.
        return "unknown"


def _hostname() -> str:
    """The machine's hostname, or ``"unknown"`` when unavailable."""
    try:
        return socket.gethostname()
    except OSError:
        # A missing hostname must not stop a command from being logged.
        return "unknown"
