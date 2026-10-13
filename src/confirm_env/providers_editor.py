"""The Textual GUI for editing the saved provider mappings.

A *provider* maps a short name to a store path (see
:mod:`confirm_env.providers`). Providers are plain global config, so unlike
the store editor this one needs no passphrase and no store to open.

Rows are shown in the same key/value table used for the store's variables,
but the value is chosen with a filesystem browser instead of being typed,
so a provider can never be saved pointing at a path that does not exist.

Nothing is written back to the providers file until ``Ctrl-S``; quitting
with unsaved changes asks for confirmation first.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, override

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
from textual.widgets import Footer, Header, Static

from confirm_env import providers
from confirm_env.editor import DIALOG_CSS, ConfirmScreen, KeyValueEditor


class ProvidersApp(App[None]):
    """The provider-mapping editor."""

    TITLE = "confirm-env providers"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+q", "request_quit", "Quit"),
    ]

    CSS = (
        DIALOG_CSS
        + """
    #providers {
        padding: 1;
    }
    #override-notice {
        background: $warning 20%;
        padding: 0 1;
    }
    """
    )

    def __init__(self, *, path: Path | None = None) -> None:
        super().__init__()
        self._path = path
        self._dirty = False

    @property
    def dirty(self) -> bool:
        """Whether the editor has unsaved changes."""
        return self._dirty

    @override
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        if providers.ENV_VAR in os.environ:
            yield Static(
                f"{providers.ENV_VAR} is set and overrides this file; "
                "edit it to change the active providers.",
                id="override-notice",
            )
        yield KeyValueEditor(
            name_editable=True,
            value_label="Store",
            add_label="Add provider",
            pick_value=True,
            widget_id="providers",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self._path or providers.providers_path())
        editor = self.query_one("#providers", KeyValueEditor)
        try:
            editor.load(providers.load(self._path))
        except ValueError as exc:
            editor.load({})
            self.notify(
                f"Could not read the providers file: {exc}",
                severity="error",
            )
        self._dirty = False

    def on_key_value_editor_changed(
        self,
        event: KeyValueEditor.Changed,
    ) -> None:
        self._dirty = True

    def action_save(self) -> None:
        rows = self.query_one("#providers", KeyValueEditor).values()
        try:
            providers.save(rows, self._path)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error")
            return
        self._dirty = False
        self.notify("Saved")

    def action_request_quit(self) -> None:
        if not self._dirty:
            self.exit()
            return
        self.push_screen(
            ConfirmScreen(
                "You have unsaved changes. Discard them?",
                title="Unsaved changes",
                confirm_label="Discard",
            ),
            self._on_quit_confirmed,
        )

    def _on_quit_confirmed(self, discard: bool) -> None:
        if discard:
            self.exit()
