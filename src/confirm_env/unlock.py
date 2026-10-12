"""Interactive unlocking: the target banner, the cancellable delay and the
passphrase prompt.

Unlocking a store is deliberately a slightly conspicuous act. Before any
passphrase is read the caller announces *what* is being unlocked and gives
the user a configurable delay, with a visible progress bar, so that an
accidental invocation can be noticed and cancelled.
"""

from __future__ import annotations

import getpass
import select
import sys
import termios
import time
import tty
from collections.abc import Callable, Iterable
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)

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
        self._saved: list[object] | None = None

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
        readable, _, _ = select.select([self._fd], [], [], 0)
        if not readable:
            return False
        return sys.stdin.read(1) in self._keys


def confirm_delay(
    seconds: float,
    *,
    description: str = "Unlocking",
    console: Console | None = None,
    sleep: Callable[[float], None] = time.sleep,
    cancel_check: Callable[[], bool] | None = None,
) -> bool:
    """Show a progress bar for ``seconds`` seconds.

    Returns ``True`` if the delay elapses, ``False`` if the user cancels
    with a cancel key, Ctrl-C, or ``cancel_check`` reporting ``True``.
    """
    if seconds < 0:
        raise ValueError("delay must not be negative")
    if seconds == 0:
        return True
    console = console or Console()
    is_cancelled = cancel_check or (lambda: False)
    try:
        with (
            KeyListener() as listener,
            Progress(
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
                console=console,
                transient=True,
            ) as progress,
        ):
            task = progress.add_task(description, total=seconds)
            start = time.monotonic()
            while True:
                elapsed = time.monotonic() - start
                if elapsed >= seconds:
                    progress.update(task, completed=seconds)
                    return True
                if is_cancelled() or listener.cancelled():
                    console.print("[yellow]Cancelled.[/yellow]")
                    return False
                progress.update(task, completed=elapsed)
                sleep(min(0.1, seconds - elapsed))
    except KeyboardInterrupt:
        console.print("[yellow]Cancelled.[/yellow]")
        return False


def announce_target(
    store: Path,
    *,
    command: list[str] | None = None,
    console: Console | None = None,
) -> None:
    """Print what is about to be unlocked and why."""
    console = console or Console()
    console.print(f"Store:   [bold]{store}[/bold]")
    if command:
        console.print(f"Command: [bold]{' '.join(command)}[/bold]")


def prompt_passphrase(question: str = "Passphrase: ") -> str:
    """Read a passphrase without echoing it."""
    return getpass.getpass(question)


def confirm(question: str, *, default: bool = False) -> bool:
    """Ask a yes/no question on the terminal."""
    marker = "[Y/n]" if default else "[y/N]"
    answer = input(f"{question} {marker} ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def prompt_new_passphrase(
    *,
    console: Console | None = None,
    read: Callable[[str], str] = prompt_passphrase,
) -> str | None:
    """Ask for a passphrase for a brand new store.

    Returns the passphrase, or ``None`` when the user opts into an
    unencrypted store by entering an empty passphrase and confirming.
    """
    console = console or Console()
    while True:
        first = read("New passphrase (empty disables encryption): ")
        if first:
            if first != read("Repeat passphrase: "):
                console.print(
                    "[red]Passphrases did not match, try again.[/red]"
                )
                continue
            return first
        console.print(
            "[yellow]An empty passphrase creates an *unencrypted* store; "
            "SQLCipher will not be used.[/yellow]",
        )
        if confirm("Create an unencrypted store?"):
            return None
