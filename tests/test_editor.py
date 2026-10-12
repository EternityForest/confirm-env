"""Tests for the Textual editor, driven through its headless pilot."""

from __future__ import annotations

from pathlib import Path

from textual.widgets import Button, DataTable, Input

from confirm_env import sidecar
from confirm_env.database import Database
from confirm_env.editor import EditorApp, KeyValueEditor
from confirm_env.sidecar import Sidecar


def _make_store(tmp_path: Path) -> tuple[Path, Database, Sidecar]:
    path = tmp_path / "edit.db"
    record = sidecar.create(None)
    database = Database.create(path, None)
    database.set_env("EXISTING", "value")
    database.set_metadata("owner", "team")
    return path, database, record


async def test_editor_loads_existing_rows(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test():
        editor = app.query_one("#variables", KeyValueEditor)
        assert editor.values() == {"EXISTING": "value"}
    database.close()


async def test_editor_adds_variable_and_saves(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        editor = app.query_one("#variables", KeyValueEditor)
        editor.query_one(DataTable).focus()

        await pilot.press("a")
        await pilot.pause()
        modal = app.screen
        modal.query_one("#row-name", Input).value = "NEW"
        modal.query_one("#row-value", Input).value = "fresh"
        modal.query_one("#save", Button).press()
        await pilot.pause()

        assert editor.values()["NEW"] == "fresh"
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert database.get_env("NEW") == "fresh"
    database.close()


async def test_editor_deletes_variable_after_confirmation(
    tmp_path: Path,
) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        editor = app.query_one("#variables", KeyValueEditor)
        editor.query_one(DataTable).focus()

        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert "EXISTING" not in editor.values()

        await pilot.press("ctrl+s")
        await pilot.pause()

    assert database.get_env("EXISTING") is None
    database.close()


async def test_editor_delete_cancel_keeps_row(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        editor = app.query_one("#variables", KeyValueEditor)
        editor.query_one(DataTable).focus()

        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()

        assert "EXISTING" in editor.values()
    database.close()


async def test_editor_saves_unlock_delay_to_sidecar(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        app.query_one("#unlock-delay", Input).value = "2"
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert sidecar.load(path).settings.unlock_delay == 2.0
    database.close()


async def test_editor_saves_metadata(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        pane = app.query_one("#metadata")
        editor = pane.query_one(KeyValueEditor)
        editor.query_one(DataTable).focus()

        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#row-name", Input).value = "note"
        app.screen.query_one("#row-value", Input).value = "hello"
        app.screen.query_one("#save", Button).press()
        await pilot.pause()

        await pilot.press("ctrl+s")
        await pilot.pause()

    assert database.list_metadata()["note"] == "hello"
    database.close()


async def test_editor_rejects_bad_delay(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        app.query_one("#unlock-delay", Input).value = "not a number"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.dirty is True  # save was rejected, changes remain
    database.close()
