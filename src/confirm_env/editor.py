"""The Textual GUI for editing a confirm-env store.

The editor has two pages inside a :class:`TabbedContent`:

* **Variables** - a table of environment variables that can be added,
  edited and deleted. Add/edit use a modal dialog; delete asks for
  confirmation.
* **Metadata & Settings** - free-form metadata (edited with the same
  key/value table) plus application settings such as the unlock delay.

Nothing is written back to the database until the user saves, and quitting
with unsaved changes asks for confirmation first.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from confirm_env import sidecar
from confirm_env.database import Database
from confirm_env.sidecar import Settings, Sidecar


class EditRowScreen(ModalScreen[tuple[str, str] | None]):
    """A modal dialog for adding or editing one key/value row."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    EditRowScreen {
        align: center middle;
    }
    """

    def __init__(
        self,
        *,
        title: str = "Edit",
        name: str = "",
        value: str = "",
        name_editable: bool = True,
    ) -> None:
        super().__init__()
        self._title = title
        self._initial_name = name
        self._initial_value = value
        self._name_editable = name_editable

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Label("Name")
            yield Input(
                value=self._initial_name,
                id="row-name",
                disabled=not self._name_editable,
            )
            yield Label("Value")
            yield Input(value=self._initial_value, id="row-value")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        name_input = self.query_one("#row-name", Input)
        if self._name_editable and not self._initial_name:
            name_input.focus()
        else:
            self.query_one("#row-value", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self._submit()
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        name = self.query_one("#row-name", Input).value.strip()
        value = self.query_one("#row-value", Input).value
        if not name:
            self.notify("Name must not be empty", severity="error")
            return
        self.dismiss((name, value))


class ConfirmScreen(ModalScreen[bool]):
    """A modal yes/no confirmation dialog."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }
    """

    def __init__(
        self,
        message: str,
        *,
        title: str = "Confirm",
        confirm_label: str = "Delete",
    ) -> None:
        super().__init__()
        self._message = message
        self._title = title
        self._confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._message)
            with Horizontal(classes="dialog-buttons"):
                yield Button(
                    self._confirm_label,
                    variant="error",
                    id="confirm",
                )
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)


class KeyValueEditor(Widget):
    """A table of key/value rows with add, edit and delete actions."""

    class Changed(Message):
        """Posted whenever the set of rows changes."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
    ]

    DEFAULT_CSS = """
    KeyValueEditor {
        height: 1fr;
    }
    KeyValueEditor DataTable {
        height: 1fr;
    }
    KeyValueEditor #row-actions {
        height: auto;
    }
    """

    def __init__(
        self,
        *,
        name_editable: bool = True,
        value_label: str = "Value",
        add_label: str = "Add",
        widget_id: str | None = None,
    ) -> None:
        super().__init__(id=widget_id)
        self._rows: dict[str, str] = {}
        self._name_editable = name_editable
        self._value_label = value_label
        self._add_label = add_label

    def compose(self) -> ComposeResult:
        yield DataTable(id="rows", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="row-actions"):
            yield Button(self._add_label, variant="success", id="add")
            yield Button("Edit", id="edit")
            yield Button("Delete", variant="error", id="delete")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns("Name", self._value_label)

    # -- data --------------------------------------------------------------

    def load(self, rows: dict[str, str]) -> None:
        """Replace all rows, e.g. from a freshly read database."""
        self._rows = dict(rows)
        self._rebuild()

    def values(self) -> dict[str, str]:
        """Return the current rows as a fresh mapping."""
        return dict(self._rows)

    def _rebuild(self) -> None:
        table = self.query_one(DataTable)
        table.clear()
        for name in sorted(self._rows):
            table.add_row(name, self._rows[name], key=name)

    def _selected_name(self) -> str | None:
        table = self.query_one(DataTable)
        if table.row_count == 0 or table.cursor_row < 0:
            return None
        coordinate = table.cursor_coordinate
        cell_key = table.coordinate_to_cell_key(coordinate)
        return str(cell_key.row_key.value)

    # -- actions -----------------------------------------------------------

    def action_add(self) -> None:
        self.app.push_screen(
            EditRowScreen(
                title=self._add_label,
                name_editable=self._name_editable,
            ),
            self._on_add,
        )

    def action_edit(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("Nothing selected", severity="warning")
            return
        self.app.push_screen(
            EditRowScreen(
                title="Edit",
                name=name,
                value=self._rows[name],
                name_editable=False,
            ),
            self._on_edit,
        )

    def action_delete(self) -> None:
        name = self._selected_name()
        if name is None:
            self.notify("Nothing selected", severity="warning")
            return
        self.app.push_screen(
            ConfirmScreen(
                f"Delete '{name}'?",
                title="Delete",
            ),
            lambda confirmed: self._on_delete(name, confirmed),
        )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_edit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = {
            "add": self.action_add,
            "edit": self.action_edit,
            "delete": self.action_delete,
        }.get(event.button.id or "")
        if action is not None:
            action()

    # -- callbacks ---------------------------------------------------------

    def _on_add(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        name, value = result
        self._rows[name] = value
        self._rebuild()
        self.post_message(self.Changed())

    def _on_edit(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        name, value = result
        self._rows[name] = value
        self._rebuild()
        self.post_message(self.Changed())

    def _on_delete(self, name: str, confirmed: bool) -> None:
        if not confirmed or name not in self._rows:
            return
        del self._rows[name]
        self._rebuild()
        self.post_message(self.Changed())


class MetadataPane(Widget):
    """Metadata key/value table plus application settings."""

    DEFAULT_CSS = """
    MetadataPane {
        height: 1fr;
    }
    MetadataPane .section-title {
        text-style: bold;
        padding: 1 0 0 0;
    }
    MetadataPane #unlock-delay {
        width: 20;
    }
    MetadataPane #setting-row {
        height: auto;
        padding: 0 0 1 0;
    }
    """

    def __init__(self, *, widget_id: str | None = None) -> None:
        super().__init__(id=widget_id)
        self._editor = KeyValueEditor(
            name_editable=True,
            value_label="Value",
            add_label="Add metadata",
            widget_id="metadata-editor",
        )

    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Metadata", classes="section-title")
            yield self._editor
            yield Static("Settings", classes="section-title")
            with Horizontal(id="setting-row"):
                yield Label("Unlock delay (seconds) ")
                yield Input(id="unlock-delay")

    def load(self, metadata: dict[str, str], unlock_delay: float) -> None:
        self._editor.load(metadata)
        self.query_one("#unlock-delay", Input).value = repr(unlock_delay)

    def metadata(self) -> dict[str, str]:
        return self._editor.values()

    def unlock_delay(self) -> str:
        return self.query_one("#unlock-delay", Input).value.strip()


class EditorApp(App[None]):
    """The confirm-env GUI."""

    TITLE = "confirm-env"

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+q", "request_quit", "Quit"),
    ]

    CSS = """
    .dialog {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    .dialog-title {
        text-style: bold;
        padding: 0 0 1 0;
    }
    .dialog-buttons {
        height: auto;
        align-horizontal: right;
        padding: 1 0 0 0;
    }
    .dialog-buttons Button {
        margin-left: 2;
    }
    #variables {
        padding: 1;
    }
    """

    def __init__(
        self,
        database: Database,
        store_path: Path,
        sidecar_obj: Sidecar,
    ) -> None:
        super().__init__()
        self._database = database
        self._store = store_path
        self._sidecar = sidecar_obj
        self._dirty = False

    @property
    def dirty(self) -> bool:
        """Whether the editor has unsaved changes."""
        return self._dirty

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent():
            with TabPane("Variables", id="tab-variables"):
                yield KeyValueEditor(widget_id="variables")
            with TabPane("Metadata & Settings", id="tab-metadata"):
                yield MetadataPane(widget_id="metadata")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self._store)
        variables = self.query_one("#variables", KeyValueEditor)
        variables.load(self._database.list_env())
        pane = self.query_one("#metadata", MetadataPane)
        pane.load(
            self._database.list_metadata(),
            self._sidecar.settings.unlock_delay,
        )
        self._dirty = False

    # -- change tracking ---------------------------------------------------

    def on_key_value_editor_changed(
        self,
        event: KeyValueEditor.Changed,
    ) -> None:
        self._dirty = True

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "unlock-delay":
            self._dirty = True

    # -- actions -----------------------------------------------------------

    def action_save(self) -> None:
        variables = self.query_one("#variables", KeyValueEditor).values()
        pane = self.query_one("#metadata", MetadataPane)
        metadata = pane.metadata()
        raw_delay = pane.unlock_delay()
        try:
            delay = float(raw_delay)
        except ValueError:
            self.notify("Unlock delay must be a number", severity="error")
            return
        if delay < 0:
            self.notify("Unlock delay must not be negative", severity="error")
            return
        try:
            updated = replace(
                self._sidecar,
                settings=Settings(unlock_delay=delay),
            )
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        try:
            self._database.replace_env(variables)
            self._database.replace_metadata(metadata)
            sidecar.save(self._store, updated)
        except (ValueError, OSError) as exc:
            self.notify(str(exc), severity="error")
            return
        self._sidecar = updated
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
