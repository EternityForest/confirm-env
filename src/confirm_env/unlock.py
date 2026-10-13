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
from collections.abc import Callable
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from confirm_env import sidecar

from .key_listener import KeyListener

#: Keys that cancel the command splash: Escape and Ctrl-C. Every other key
#: passes the gate, as the splash asks for "any key".
SPLASH_CANCEL_KEYS = frozenset({"\x1b", "\x03"})





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
        lines.append(("Unlocking for", " ".join(command)))

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
