"""Command line interface for confirm-env.

Modes:

``confirm-env -e FILE``
    Open the editor for ``FILE``, creating the store if it does not exist.

``confirm-env FILE COMMAND [ARGS...]``
    Unlock ``FILE`` and run ``COMMAND`` with its variables in the
    environment. ``COMMAND``'s exit status is forwarded unchanged.

``confirm-env --provider NAME COMMAND [ARGS...]``
    Resolve the store from a saved provider instead of naming ``FILE``.

``confirm-env --set-provider NAME FILE``
    Record ``FILE`` as the store for ``NAME`` and exit.

``confirm-env --edit-providers``
    Open a GUI for adding, editing and deleting saved providers.

All options must appear before the store; everything after it is the
command and its arguments.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from confirm_env import (
    __version__,
    log,
    migrate,
    picker,
    providers,
    providers_editor,
    sidecar,
)
from confirm_env.database import Database, UnlockError
from confirm_env.delay import confirm_delay
from confirm_env.editor import EditorApp
from confirm_env.sidecar import Sidecar
from confirm_env.unlock import (
    announce_command_splash,
    announce_target,
    prompt_math_lock,
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
        "--print",
        dest="print_env",
        action="store_true",
        help="print the variables as shell 'export' lines",
    )
    parser.add_argument(
        "--provider",
        default=None,
        metavar="NAME",
        help="resolve the store from the saved provider NAME",
    )
    parser.add_argument(
        "--set-provider",
        nargs=2,
        default=None,
        metavar=("NAME", "FILE"),
        help="record FILE as the store for provider NAME and exit",
    )
    parser.add_argument(
        "--edit-providers",
        action="store_true",
        help="open the GUI for editing saved providers and exit",
    )
    parser.add_argument(
        "--delay",
        type=_delay_seconds,
        default=None,
        metavar="SECONDS",
        help="Extra delay after fully unlocking, before running the command",
    )
    parser.add_argument(
        "--title",
        default=None,
        metavar="TEXT",
        help="show a confirm box with this title before unlocking",
    )
    parser.add_argument(
        "--description",
        default=None,
        metavar="TEXT",
        help="body text for the confirm box (used with --title)",
    )
    parser.add_argument(
        "file",
        type=Path,
        nargs="?",
        default=None,
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
    database = Database.create(store, key, record.cipher_compatibility)
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
    console: Console | None = None,
) -> tuple[Database, Sidecar]:
    """Announce, delay, prompt and open an existing store."""
    console = console or Console()
    migrate.recover(store)
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
    console.print("\n")
    announce_target(store, command=command, console=console)
    if not record.encrypted:
        return Database.open(store, None), record
    delay = record.lock_settings.unlock_delay

    if not confirm_delay(
        delay,
        description=f"Unlocking {store.name}",
        console=console,
    ):
        raise UserCancelledError

    digits = record.lock_settings.math_lock_digits
    failures = 0
    while failures < MAX_ATTEMPTS:
        if digits and not prompt_math_lock(digits):
            failures += 1
            console.print("[red]Wrong answer.[/red]")
            continue
        prompt = "Passphrase: " if failures == 0 else "Passphrase (try again): "
        passphrase = prompt_passphrase(prompt)
        try:
            key = record.derive_key(passphrase)
            return Database.open(
                store,
                key,
                record.cipher_compatibility,
            ), record
        except UnlockError:
            failures += 1
            console.print("[red]Wrong passphrase.[/red]")
    raise CliError("too many failed unlock attempts")


def run_editor(
    store: Path,
    console: Console,
) -> int:
    """Open the editor, creating the store if it does not exist."""
    if sidecar.exists(store):
        database, record = unlock_store(
            store,
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
    console: Console,
) -> int:
    """Print the store's variables as shell ``export`` lines."""
    database, _ = unlock_store(
        store,
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
    console: Console,
    extra_delay: float | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
) -> int:
    """Unlock the store and run ``command`` with its variables set."""
    if (title or description) and not announce_command_splash(
        title or "",
        description or "",
        console=console,
    ):
        raise UserCancelledError
    database, _ = unlock_store(
        store,
        command=command,
        console=console,
    )
    try:
        variables = database.list_env()
        database.append_log(log.capture(command))
    finally:
        database.close()

    if (extra_delay is not None) and not confirm_delay(
            extra_delay,
            description="About to run command",
            console=console,
        ):
            raise UserCancelledError
    environment = dict(os.environ)
    environment.update(variables)
    try:
        completed = subprocess.run(command, env=environment)
    except FileNotFoundError as exc:
        raise CliError(f"command not found: {command[0]}") from exc
    return completed.returncode


def run_set_provider(name: str, store: Path, console: Console) -> int:
    """Record a provider mapping and exit."""
    try:
        providers.set_provider(name, store)
    except (ValueError, FileNotFoundError) as exc:
        raise CliError(str(exc)) from exc
    console.print(f"[green]Provider[/green] {name} -> {store}")
    return 0


def run_providers_editor() -> int:
    """Open the GUI for editing the saved providers."""
    providers_editor.ProvidersApp().run()
    return 0


def resolve_provider(name: str, console: Console) -> Path:
    """Resolve ``name`` to a store path, opening a picker when unknown."""
    resolved = providers.lookup(name)
    if resolved is not None:
        if resolved.is_file():
            return resolved
        raise CliError(
            f"provider {name!r} points at missing file {resolved}; "
            f"reset it with 'confirm-env --set-provider {name} FILE'",
        )
    if not sys.stdin.isatty():
        raise CliError(
            f"no provider named {name!r}; set one with "
            f"'confirm-env --set-provider {name} FILE'",
        )
    chosen = picker.pick_file()
    if chosen is None:
        raise UserCancelledError
    try:
        providers.set_provider(name, chosen)
    except (ValueError, FileNotFoundError) as exc:
        raise CliError(str(exc)) from exc
    console.print(f"[green]Saved provider[/green] {name} -> {chosen}")
    return chosen


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.set_provider is not None:
            name, store = args.set_provider
            return run_set_provider(name, Path(store), console)
        if args.edit_providers:
            return run_providers_editor()
        command = list(args.command)
        store = args.file
        if args.provider is not None:
            # Provider mode has no FILE positional, so argparse hands the
            # first command token to ``file``; put it back on the command.
            if store is not None:
                command = [str(store), *command]
                store = None
            store = resolve_provider(args.provider, console)
        if store is None:
            parser.error("a FILE is required, or use --provider NAME")
        if args.edit:
            return run_editor(store, console)
        if args.print_env:
            if command:
                parser.error("--print does not accept a command")
            return print_env(store, console)
        if not command:
            parser.error(
                "a command is required, or use -e/--edit to edit the store",
            )
        return run_command(
            store,
            command,
            console,
            title=args.title,
            extra_delay=args.delay,
            description=args.description,
        )
    except UserCancelledError:
        console.print("[yellow]Cancelled.[/yellow]")
        return EXIT_CANCELLED
    except KeyboardInterrupt:
        console.print("[yellow]Interrupted.[/yellow]")
        return EXIT_CANCELLED
    except (CliError, UnlockError) as exc:
        console.print("[red]Error:[/red]", str(exc))
        return EXIT_ERROR
