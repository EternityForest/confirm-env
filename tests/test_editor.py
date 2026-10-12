"""Tests for the Textual editor, driven through its headless pilot."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from textual.widgets import Button, DataTable, Input

from confirm_env import sidecar
from confirm_env.database import Database, UnlockError
from confirm_env.editor import EditorApp, KeyValueEditor
from confirm_env.sidecar import Sidecar

OLD_PASSPHRASE = "old passphrase"
NEW_PASSPHRASE = "new passphrase"


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


async def test_editor_saves_math_lock_digits(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        app.query_one("#math-lock-digits", Input).value = "3"
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert sidecar.load(path).settings.math_lock_digits == 3
    database.close()


async def test_editor_rejects_bad_math_lock_digits(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        app.query_one("#math-lock-digits", Input).value = "lots"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.dirty is True  # save was rejected, changes remain
    database.close()


async def test_editor_saves_splash(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        app.query_one("#database-title", Input).value = "Personal"
        app.query_one("#database-description", Input).value = "Home server"
        await pilot.press("ctrl+s")
        await pilot.pause()

    loaded = sidecar.load(path)
    assert loaded.database_title == "Personal"
    assert loaded.database_description == "Home server"
    database.close()


async def test_editor_loads_existing_splash(tmp_path: Path) -> None:
    path, database, record = _make_store(tmp_path)
    record = replace(
        record,
        database_title="Personal",
        database_description="Home server",
    )
    app = EditorApp(database, path, record)
    async with app.run_test():
        assert app.query_one("#database-title", Input).value == "Personal"
        assert (
            app.query_one("#database-description", Input).value == "Home server"
        )
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


def _make_encrypted_store(
    tmp_path: Path,
) -> tuple[Path, Database, Sidecar]:
    path = tmp_path / "secure.db"
    record = sidecar.create(OLD_PASSPHRASE)
    key = record.derive_key(OLD_PASSPHRASE)
    database = Database.create(path, key, record.cipher_compatibility)
    database.set_env("EXISTING", "value")
    sidecar.save(path, record)
    return path, database, record


async def test_editor_changes_passphrase(tmp_path: Path) -> None:
    path, database, record = _make_encrypted_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        app.query_one("#change-passphrase", Button).press()
        await pilot.pause()
        modal = app.screen
        modal.query_one("#new-passphrase", Input).value = NEW_PASSPHRASE
        modal.query_one("#repeat-passphrase", Input).value = NEW_PASSPHRASE
        modal.query_one("#save", Button).press()
        await pilot.pause()

    new_record = sidecar.load(path)
    assert new_record.salt != record.salt
    new_key = new_record.derive_key(NEW_PASSPHRASE)
    reopened = Database.open(path, new_key, new_record.cipher_compatibility)
    try:
        assert reopened.get_env("EXISTING") == "value"
    finally:
        reopened.close()
    with pytest.raises(UnlockError):
        Database.open(path, record.derive_key(OLD_PASSPHRASE))


async def test_editor_rejects_mismatched_passphrase(tmp_path: Path) -> None:
    path, database, record = _make_encrypted_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        app.query_one("#change-passphrase", Button).press()
        await pilot.pause()
        modal = app.screen
        modal.query_one("#new-passphrase", Input).value = NEW_PASSPHRASE
        modal.query_one("#repeat-passphrase", Input).value = "different"
        modal.query_one("#save", Button).press()
        await pilot.pause()
        assert app.screen is modal  # the dialog stayed open
    assert sidecar.load(path).salt == record.salt
    database.close()


async def test_editor_saves_before_changing_passphrase(tmp_path: Path) -> None:
    path, database, record = _make_encrypted_store(tmp_path)
    app = EditorApp(database, path, record)
    async with app.run_test() as pilot:
        editor = app.query_one("#variables", KeyValueEditor)
        editor.query_one(DataTable).focus()
        await pilot.press("a")
        await pilot.pause()
        modal = app.screen
        modal.query_one("#row-name", Input).value = "UNSAVED"
        modal.query_one("#row-value", Input).value = "value"
        modal.query_one("#save", Button).press()
        await pilot.pause()
        assert app.dirty is True

        app.query_one("#change-passphrase", Button).press()
        await pilot.pause()
        passphrase_modal = app.screen
        passphrase_modal.query_one(
            "#new-passphrase", Input
        ).value = NEW_PASSPHRASE
        passphrase_modal.query_one(
            "#repeat-passphrase", Input
        ).value = NEW_PASSPHRASE
        passphrase_modal.query_one("#save", Button).press()
        await pilot.pause()

    new_record = sidecar.load(path)
    new_key = new_record.derive_key(NEW_PASSPHRASE)
    reopened = Database.open(path, new_key, new_record.cipher_compatibility)
    try:
        assert reopened.get_env("UNSAVED") == "value"
    finally:
        reopened.close()
