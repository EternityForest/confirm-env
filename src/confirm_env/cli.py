"""Command line interface for confirm-env.

Two modes are supported:

``confirm-env -e FILE``
    Open the editor for ``FILE``, creating the store if it does not exist.

``confirm-env FILE COMMAND [ARGS...]``
    Unlock ``FILE`` and run ``COMMAND`` with its variables in the
    environment. ``COMMAND``'s exit status is forwarded unchanged.

All options must appear before ``FILE``; everything after ``FILE`` is the
command and its arguments.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
from pathlib import Path

from rich.console import Console

from confirm_env import __version__, sidecar
from confirm_env.database import Database, UnlockError
from confirm_env.editor import EditorApp
from confirm_env.sidecar import Sidecar
from confirm_env.unlock import (
    announce_target,
    confirm_delay,
    prompt_new_passphrase,
    prompt_passphrase,
)

#: Exit code returned when a run command fails to start.
EXIT_ERROR = 1

#: Exit code returned when the user cancels an interactive prompt.
EXIT_CANCELLED = 130

#: How many times to re-prompt after a wrong passphrase.
MAX_ATTEMPTS = 3


class CliError(Exception):
    """A user facing error, printed without a traceback."""


class UserCancelledError(Exception):
    """The user cancelled an interactive prompt."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="confirm-env",
        description=(
            "Run a command with environment variables kept in an "
            "encrypted database, or edit that database."
        ),
        epilog=(
            "options must precede FILE; everything after FILE is run as "
            "the command"
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-e",
        "--edit",
        action="store_true",
        help="open the editor for FILE, creating it if needed",
    )
    parser.add_argument(
        "-p",
        "--print",
        dest="print_env",
        action="store_true",
        help="print the variables as shell 'export' lines",
    )
    parser.add_argument(
        "--delay",
        type=_delay_seconds,
        default=None,
        metavar="SECONDS",
        help="override the configured unlock delay",
    )
    parser.add_argument(
        "file",
        type=Path,
        help="the confirm-env database to use",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="the command to run, with its arguments",
    )
    return parser


def _delay_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if seconds < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return seconds


def export_lines(variables: dict[str, str]) -> list[str]:
    """Render variables as POSIX shell ``export`` lines."""
    return [
        f"export {name}={shlex.quote(value)}"
        for name, value in sorted(variables.items())
    ]


def create_store(store: Path, console: Console) -> tuple[Database, Sidecar]:
    """Interactively create a new store and its sidecar."""
    console.print(f"[bold]Creating new store:[/bold] {store}")
    passphrase = prompt_new_passphrase(console=console)
    record = sidecar.create(passphrase)
    key = record.derive_key(passphrase) if passphrase else None
    database = Database.create(store, key)
    try:
        sidecar.save(store, record)
    except BaseException:
        database.close()
        store.unlink(missing_ok=True)
        raise
    return database, record


def unlock_store(
    store: Path,
    *,
    command: list[str] | None = None,
    delay_override: float | None = None,
    console: Console | None = None,
) -> tuple[Database, Sidecar]:
    """Announce, delay, prompt and open an existing store."""
    console = console or Console()
    if not sidecar.exists(store):
        if store.exists():
            raise CliError(
                f"missing sidecar file for {store} "
                f"(expected {sidecar.sidecar_path(store)})",
            )
        raise CliError(
            f"no store at {store}; create one with 'confirm-env -e {store}'",
        )
    record = sidecar.load(store)
    announce_target(store, command=command, console=console)
    if not record.encrypted:
        return Database.open(store, None), record
    delay = (
        record.settings.unlock_delay
        if delay_override is None
        else delay_override
    )
    if not confirm_delay(
        delay,
        description=f"Unlocking {store.name}",
        console=console,
    ):
        raise UserCancelledError
    for attempt in range(MAX_ATTEMPTS):
        prompt = "Passphrase: " if attempt == 0 else "Passphrase (try again): "
        passphrase = prompt_passphrase(prompt)
        try:
            key = record.derive_key(passphrase)
            return Database.open(store, key), record
        except UnlockError:
            console.print("[red]Wrong passphrase.[/red]")
    raise CliError("too many failed unlock attempts")


def run_editor(
    store: Path,
    delay_override: float | None,
    console: Console,
) -> int:
    """Open the editor, creating the store if it does not exist."""
    if sidecar.exists(store):
        database, record = unlock_store(
            store,
            delay_override=delay_override,
            console=console,
        )
    elif store.exists():
        raise CliError(
            f"missing sidecar file for {store} "
            f"(expected {sidecar.sidecar_path(store)})",
        )
    else:
        database, record = create_store(store, console)
    try:
        EditorApp(database, store, record).run()
    finally:
        database.close()
    return 0


def print_env(
    store: Path,
    delay_override: float | None,
    console: Console,
) -> int:
    """Print the store's variables as shell ``export`` lines."""
    database, _ = unlock_store(
        store,
        delay_override=delay_override,
        console=console,
    )
    try:
        variables = database.list_env()
    finally:
        database.close()
    for line in export_lines(variables):
        print(line)
    return 0


def run_command(
    store: Path,
    command: list[str],
    delay_override: float | None,
    console: Console,
) -> int:
    """Unlock the store and run ``command`` with its variables set."""
    database, _ = unlock_store(
        store,
        command=command,
        delay_override=delay_override,
        console=console,
    )
    try:
        variables = database.list_env()
    finally:
        database.close()
    environment = dict(os.environ)
    environment.update(variables)
    try:
        completed = subprocess.run(command, env=environment)
    except FileNotFoundError as exc:
        raise CliError(f"command not found: {command[0]}") from exc
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.edit:
            return run_editor(args.file, args.delay, console)
        command = list(args.command)
        if args.print_env:
            if command:
                parser.error("--print does not accept a command")
            return print_env(args.file, args.delay, console)
        if not command:
            parser.error(
                "a command is required, or use -e/--edit to edit the store",
            )
        return run_command(args.file, command, args.delay, console)
    except UserCancelledError:
        console.print("[yellow]Cancelled.[/yellow]")
        return EXIT_CANCELLED
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")
        return EXIT_CANCELLED
    except (CliError, UnlockError) as exc:
        console.print("[red]Error:[/red]", str(exc))
        return EXIT_ERROR
