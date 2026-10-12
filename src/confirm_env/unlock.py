"""Interactive unlocking: the target banner, the cancellable delay and the
passphrase prompt.

Unlocking a store is deliberately a slightly conspicuous act. Before any
passphrase is read the caller announces *what* is being unlocked and gives
the user a configurable delay, with a visible progress bar, so that an
accidental invocation can be noticed and cancelled.
"""

from __future__ import annotations

import getpass
import random
import select
import sys
import termios
import time
import tty
from collections.abc import Callable, Iterable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
)

from confirm_env import sidecar

#: Keys that cancel the delay: ``q``, Escape and Ctrl-C (in cbreak mode
#: Ctrl-C arrives as the ETX byte rather than a signal).
CANCEL_KEYS = frozenset({"q", "\x1b", "\x03"})

#: Keys that cancel the command splash: Escape and Ctrl-C. Every other key
#: passes the gate, as the splash asks for "any key".
SPLASH_CANCEL_KEYS = frozenset({"\x1b", "\x03"})


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
    return False


def announce_target(
    store: Path,
    *,
    command: list[str] | None = None,
    console: Console | None = None,
) -> None:
    """Print what is about to be unlocked and why."""
    console = console or Console()

    lines : list[tuple[str,str]] = []

    lines.append(("Store File", str(store)))
    if command:
        lines.append(("Command", " ".join(command)))

    record = sidecar.load(store)


    p = Panel(
            "\n".join([
                f"{line[0]}: [bold]{line[1]}[/bold]"
                for line in lines
            ])+"\n\n"+(record.database_description or "No description"),
            title=record.database_title or store.name,

            subtitle="Any key to continue",
        )
    console.print(p,)


def _read_key() -> str | None:
    """Block for a single key press, or ``None`` without a terminal."""
    with KeyListener() as listener:
        return listener.wait()


def announce_command_splash(
    title: str,
    description: str,
    *,
    console: Console | None = None,
    wait: Callable[[], str | None] = _read_key,
) -> bool:
    """Show a box describing the command and wait for a key press.

    The box is displayed *before* the unlock delay starts, so the user can
    see exactly what is about to happen. Returns ``True`` when a key is
    pressed, or ``False`` if the user cancels with Escape or Ctrl-C. A
    non-interactive stdin passes straight through without consuming input.
    """
    console = console or Console()
    console.print(
        Panel(
            description or "(no description)",
            title=title or "Command",
            subtitle="Any key to continue",
        ),
    )
    key = wait()
    return key not in SPLASH_CANCEL_KEYS


def prompt_passphrase(question: str = "Passphrase: ") -> str:
    """Read a passphrase without echoing it."""
    return getpass.getpass(question)


def _math_addends(digits: int) -> tuple[int, int]:
    """Two random integers that each have exactly ``digits`` digits."""
    if digits < 1:
        raise ValueError("math lock requires at least one digit")
    low = 10 ** (digits - 1)
    high = 10**digits - 1
    return random.randint(low, high), random.randint(low, high)


def prompt_math_lock(
    digits: int,
    *,
    read: Callable[[str], str] = input,
) -> bool:
    """Present an addition challenge and report whether it was answered.

    ``digits`` is the width of each addend. The challenge is deliberately
    simple arithmetic: its job is to catch an absent-minded invocation,
    not to be a security boundary.
    """
    left, right = _math_addends(digits)
    answer = read(f"{left} + {right} = ").strip()
    try:
        value = int(answer)
    except ValueError:
        return False
    return value == left + right


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
