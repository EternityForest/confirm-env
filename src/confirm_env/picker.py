"""A Textual file picker used to locate a store for an unknown provider."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual_fspicker import FileOpen


class FilePickerApp(App[Path | None]):
    """Open the file picker on mount and return the chosen path."""

    def __init__(self, location: Path) -> None:
        super().__init__()
        self._location = location

    def on_mount(self) -> None:
        self.push_screen(FileOpen(location=self._location), self.exit)


def pick_file(location: Path | None = None) -> Path | None:
    """Run the file picker and return the chosen path.

    Returns ``None`` when the user cancels the dialog.
    """
    start = Path.cwd() if location is None else location
    return FilePickerApp(start).run()
