import time
from collections.abc import Callable

from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeRemainingColumn

from .key_listener import KeyListener


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
