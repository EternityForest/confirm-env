from __future__ import annotations

import select
import sys
import termios
import tty
from collections.abc import Iterable

#: Keys that cancel the delay: ``q``, Escape and Ctrl-C (in cbreak mode
#: Ctrl-C arrives as the ETX byte rather than a signal).
CANCEL_KEYS = frozenset({"q", "\x1b", "\x03"})

class KeyListener:
    """Read a single terminal key press without waiting for Enter.

    On a non-interactive stdin this object does nothing and never reports a
    cancellation, so the delay still works when output is piped.
    """

    def __init__(self, keys: Iterable[str] = CANCEL_KEYS) -> None:
        self._keys = frozenset(keys)
        self._fd: int | None = None
        self._saved: (
            list[int | list[bytes | int]]
            | list[int | list[bytes]]
            | list[int | list[int]]
            | None
        ) = None

    def __enter__(self) -> KeyListener:
        if sys.stdin.isatty():
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        return self

    def __exit__(self, *exc_info: object) -> bool:
        if self._fd is not None and self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
        self._fd = None
        self._saved = None
        return False

    def cancelled(self) -> bool:
        """Whether a cancel key has been pressed since the last check."""
        if self._fd is None:
            return False
        # pyrefly: ignore [implicit-any-empty-container]
        readable, _, _ = select.select([self._fd], [], [], 0)
        if not readable:
            return False
        return sys.stdin.read(1) in self._keys

    def wait(self) -> str | None:
        """Block for a single key press and return it.

        Returns ``None`` on a non-interactive stdin, where we must not
        consume input that belongs to the command being run.
        """
        if self._fd is None:
            return None
        # pyrefly: ignore [implicit-any-empty-container]
        readable, _, _ = select.select([self._fd], [], [], None)
        if not readable:
            return None
        return sys.stdin.read(1)

