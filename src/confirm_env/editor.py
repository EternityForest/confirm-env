from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import ClassVar, override

from textual.app import App, ComposeResult
from textual.binding import Binding, BindingType
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

from confirm_env import log, migrate, sidecar
from confirm_env.database import Database
from confirm_env.migrate import MigrationError
from confirm_env.sidecar import LockSettings, Sidecar

"""The Textual GUI for editing a confirm-env store.

The editor has three pages inside a :class:`TabbedContent`:

* **Variables** - a table of environment variables that can be added,
  edited and deleted. Add/edit use a modal dialog; delete asks for
  confirmation.
* **Metadata & Settings** - free-form metadata (edited with the same
  key/value table), the unlock splash title and description, and
  application settings such as the unlock delay and math lock.
* **Log** - a read-only, paginated view of the commands that have been
  run against the store.

Nothing is written back to the database until the user saves, and quitting
with unsaved changes asks for confirmation first.
"""


class EditRowScreen(ModalScreen[tuple[str, str] | None]):
    """A modal dialog for adding or editing one key/value row."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel")
    ]

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

    @override
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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel")
    ]

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

    @override
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


class PassphraseScreen(ModalScreen[str | None]):
    """A modal dialog for choosing a new store passphrase.

    Dismisses with the new passphrase, or ``None`` when cancelled. The
    passphrase must be non-empty and match its confirmation.
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel"),
    ]

    DEFAULT_CSS = """
    PassphraseScreen {
        align: center middle;
    }
    """

    def __init__(self, *, title: str = "Change passphrase") -> None:
        super().__init__()
        self._title = title

    @override
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Label("New passphrase")
            yield Input(password=True, id="new-passphrase")
            yield Label("Repeat passphrase")
            yield Input(password=True, id="repeat-passphrase")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Change", variant="primary", id="save")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one("#new-passphrase", Input).focus()

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
        first = self.query_one("#new-passphrase", Input).value
        second = self.query_one("#repeat-passphrase", Input).value
        if not first:
            self.notify("Passphrase must not be empty", severity="error")
            return
        if first != second:
            self.notify("Passphrases did not match", severity="error")
            return
        self.dismiss(first)


class KeyValueEditor(Widget):
    """A table of key/value rows with add, edit and delete actions."""

    class Changed(Message):
        """Posted whenever the set of rows changes."""

    BINDINGS: ClassVar[list[BindingType]] = [
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

    @override
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
            lambda confirmed: self._on_delete(name, confirmed or False),
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

    class ChangePassphraseRequested(Message):
        """Posted when the user asks to change the store passphrase."""

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
    MetadataPane #math-lock-digits {
        width: 20;
    }
    MetadataPane #database-title {
        width: 1fr;
    }
    MetadataPane #database-description {
        width: 1fr;
    }
    MetadataPane .setting-row {
        height: auto;
        padding: 0 0 1 0;
    }
    MetadataPane #change-passphrase {
        margin: 0 0 1 0;
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

    @override
    def compose(self) -> ComposeResult:
        with VerticalScroll():
            yield Static("Metadata", classes="section-title")
            yield self._editor
            yield Static("Splash", classes="section-title")
            with Horizontal(classes="setting-row"):
                yield Label("Title ")
                yield Input(id="database-title")
            with Horizontal(classes="setting-row"):
                yield Label("Description ")
                yield Input(id="database-description")
            yield Static("Settings", classes="section-title")
            with Horizontal(classes="setting-row"):
                yield Label("Unlock delay (seconds) ")
                yield Input(id="unlock-delay")
            with Horizontal(classes="setting-row"):
                yield Label("Math lock digits ")
                yield Input(id="math-lock-digits")
            yield Button("Change passphrase...", id="change-passphrase")

    def load(self, metadata: dict[str, str], record: Sidecar) -> None:
        self._editor.load(metadata)
        self.query_one("#database-title", Input).value = record.database_title
        self.query_one(
            "#database-description", Input
        ).value = record.database_description
        self.query_one("#unlock-delay", Input).value = repr(
            record.lock_settings.unlock_delay
        )
        self.query_one("#math-lock-digits", Input).value = str(
            record.lock_settings.math_lock_digits
        )

    def metadata(self) -> dict[str, str]:
        return self._editor.values()

    def database_title(self) -> str:
        return self.query_one("#database-title", Input).value

    def database_description(self) -> str:
        return self.query_one("#database-description", Input).value

    def unlock_delay(self) -> str:
        return self.query_one("#unlock-delay", Input).value.strip()

    def math_lock_digits(self) -> str:
        return self.query_one("#math-lock-digits", Input).value.strip()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "change-passphrase":
            self.post_message(self.ChangePassphraseRequested())


class LogPane(Widget):
    """A read-only, paginated view of the store's run log."""

    DEFAULT_CSS = """
    LogPane {
        height: 1fr;
        padding: 1;
    }
    LogPane DataTable {
        height: 1fr;
    }
    LogPane #log-actions {
        height: auto;
        padding: 1 0 0 0;
    }
    LogPane #log-page {
        width: 1fr;
        content-align: left middle;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        database: Database,
        *,
        widget_id: str | None = None,
    ) -> None:
        super().__init__(id=widget_id)
        self._database = database
        self._page = 0

    @override
    def compose(self) -> ComposeResult:
        yield DataTable(id="log-rows", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="log-actions"):
            yield Button("Previous", id="log-previous")
            yield Button("Next", id="log-next")
            yield Label("", id="log-page")
            yield Button("Refresh", id="log-refresh")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "Time",
            "Command",
            "User",
            "Directory",
            "Host",
        )
        self.show_page(0)

    def set_database(self, database: Database) -> None:
        """Point the pane at a new connection, e.g. after a rekey."""
        self._database = database
        if self.is_mounted:
            self.show_page(self._page)

    @property
    def page_count(self) -> int:
        """The number of pages, at least one even when the log is empty."""
        total = self._database.count_log()
        return max(1, (total + log.PAGE_SIZE - 1) // log.PAGE_SIZE)

    def show_page(self, page: int) -> None:
        """Show ``page`` (zero-based), clamped to the available range."""
        self._page = max(0, min(page, self.page_count - 1))
        table = self.query_one(DataTable)
        table.clear()
        entries = self._database.log_page(
            self._page * log.PAGE_SIZE,
            log.PAGE_SIZE,
        )
        for entry in entries:
            table.add_row(
                entry.timestamp,
                entry.command,
                entry.username,
                entry.workdir,
                entry.hostname,
            )
        total = self._database.count_log()
        self.query_one("#log-page", Label).update(
            f"Page {self._page + 1} of {self.page_count} ({total} entries)"
        )
        self.query_one("#log-previous", Button).disabled = self._page == 0
        self.query_one("#log-next", Button).disabled = (
            self._page >= self.page_count - 1
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "log-previous":
            self.show_page(self._page - 1)
        elif event.button.id == "log-next":
            self.show_page(self._page + 1)
        elif event.button.id == "log-refresh":
            self.show_page(self._page)


class EditorApp(App[None]):
    """The confirm-env GUI."""

    TITLE = "confirm-env"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+p", "change_passphrase", "Passphrase"),
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

    @override
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent():
            with TabPane("Variables", id="tab-variables"):
                yield KeyValueEditor(widget_id="variables")
            with TabPane("Metadata & Settings", id="tab-metadata"):
                yield MetadataPane(widget_id="metadata")
            with TabPane("Log", id="tab-log"):
                yield LogPane(self._database, widget_id="log")
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = str(self._store)
        variables = self.query_one("#variables", KeyValueEditor)
        variables.load(self._database.list_env())
        pane = self.query_one("#metadata", MetadataPane)
        pane.load(self._database.list_metadata(), self._sidecar)
        self._dirty = False

    # -- change tracking ---------------------------------------------------

    def on_key_value_editor_changed(
        self,
        event: KeyValueEditor.Changed,
    ) -> None:
        self._dirty = True

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in {
            "unlock-delay",
            "math-lock-digits",
            "database-title",
            "database-description",
        }:
            self._dirty = True

    def on_metadata_pane_change_passphrase_requested(
        self,
        event: MetadataPane.ChangePassphraseRequested,
    ) -> None:
        self.action_change_passphrase()

    # -- actions -----------------------------------------------------------

    def action_save(self) -> None:
        variables = self.query_one("#variables", KeyValueEditor).values()
        pane = self.query_one("#metadata", MetadataPane)
        metadata = pane.metadata()
        try:
            settings = LockSettings(
                unlock_delay=_parse_delay(pane.unlock_delay()),
                math_lock_digits=_parse_digits(pane.math_lock_digits()),
            )
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        updated = replace(
            self._sidecar,
            lock_settings=settings,
            database_title=pane.database_title(),
            database_description=pane.database_description(),
        )
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

    def action_change_passphrase(self) -> None:
        self.push_screen(PassphraseScreen(), self._on_change_passphrase)

    def _on_change_passphrase(self, new_passphrase: str | None) -> None:
        if new_passphrase is None:
            return
        if self._dirty:
            self.action_save()
            if self._dirty:
                self.notify(
                    "Save your changes before changing the passphrase",
                    severity="error",
                )
                return
        old_key = self._database.key
        old_compatibility = self._sidecar.cipher_compatibility
        # Close our connection first so the store file can be replaced
        # (required on Windows, harmless elsewhere).
        self._database.close()
        try:
            new_record = migrate.reencrypt(
                self._store,
                old_key=old_key,
                old_compatibility=old_compatibility,
                new_passphrase=new_passphrase,
            )
        except (MigrationError, ValueError, OSError) as exc:
            self._database = Database.open(
                self._store,
                old_key,
                old_compatibility,
            )
            self._sync_log_pane()
            self.notify(
                f"Passphrase change failed: {exc}",
                severity="error",
            )
            return
        new_key = new_record.derive_key(new_passphrase)
        self._database = Database.open(
            self._store,
            new_key,
            new_record.cipher_compatibility,
        )
        self._sidecar = new_record
        self._sync_log_pane()
        self.notify("Passphrase changed")

    def _sync_log_pane(self) -> None:
        """Point the log view at the current connection after a rekey."""
        self.query_one("#log", LogPane).set_database(self._database)

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


def _parse_delay(raw: str) -> float:
    """Parse the unlock delay field, raising a user-facing error."""
    try:
        delay = float(raw)
    except ValueError as exc:
        raise ValueError("Unlock delay must be a number") from exc
    if delay < 0:
        raise ValueError("Unlock delay must not be negative")
    return delay


def _parse_digits(raw: str) -> int:
    """Parse the math lock digits field, raising a user-facing error."""
    try:
        digits = int(raw)
    except ValueError as exc:
        raise ValueError("Math lock digits must be a whole number") from exc
    if digits < 0:
        raise ValueError("Math lock digits must not be negative")
    return digits
