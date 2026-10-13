"""Tests for the provider-mapping editor, driven by its headless pilot."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Static

from confirm_env import editor, providers
from confirm_env.editor import ConfirmScreen, KeyValueEditor
from confirm_env.providers_editor import ProvidersApp


@pytest.fixture
def providers_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point the providers module at a throwaway file."""
    path = tmp_path / "providers.toml"
    monkeypatch.setattr(providers, "providers_path", lambda: path)
    monkeypatch.delenv(providers.ENV_VAR, raising=False)
    return path


def _install_picker(
    monkeypatch: pytest.MonkeyPatch,
    chosen: Path | None,
) -> dict[str, Path | None]:
    """Replace the file browser with a stub that returns ``chosen``."""
    seen: dict[str, Path | None] = {}

    def factory(location: Path | None = None) -> ModalScreen[Path | None]:
        seen["location"] = location

        class StubPicker(ModalScreen[Path | None]):
            def on_mount(self) -> None:
                self.dismiss(chosen)

        return StubPicker()

    monkeypatch.setattr(editor, "FileOpen", factory)
    return seen


async def test_editor_loads_existing_providers(providers_file: Path) -> None:
    providers.save({"work": "/srv/work.db"})
    app = ProvidersApp()
    async with app.run_test():
        kv = app.query_one("#providers", KeyValueEditor)
        assert kv.values() == {"work": "/srv/work.db"}


async def test_editor_adds_provider_via_browser(
    providers_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "chosen.db"
    store.write_text("x")
    _install_picker(monkeypatch, store)
    app = ProvidersApp()
    async with app.run_test() as pilot:
        kv = app.query_one("#providers", KeyValueEditor)
        kv.query_one(DataTable).focus()

        await pilot.press("a")
        await pilot.pause()
        modal = app.screen
        modal.query_one("#row-name", Input).value = "work"
        modal.query_one("#save", Button).press()
        await pilot.pause()

        assert kv.values() == {"work": str(store)}

        await pilot.press("ctrl+s")
        await pilot.pause()

    assert providers.load() == {"work": str(store)}


async def test_editor_add_cancelled_browser_adds_nothing(
    providers_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_picker(monkeypatch, None)
    app = ProvidersApp()
    async with app.run_test() as pilot:
        kv = app.query_one("#providers", KeyValueEditor)
        kv.query_one(DataTable).focus()

        await pilot.press("a")
        await pilot.pause()
        modal = app.screen
        modal.query_one("#row-name", Input).value = "work"
        modal.query_one("#save", Button).press()
        await pilot.pause()

        assert kv.values() == {}
        assert app.dirty is False


async def test_editor_edit_uses_browser_from_current_dir(
    providers_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = tmp_path / "sub" / "old.db"
    old.parent.mkdir()
    old.write_text("x")
    new = tmp_path / "sub" / "new.db"
    new.write_text("y")
    providers.save({"work": str(old)})
    seen = _install_picker(monkeypatch, new)
    app = ProvidersApp()
    async with app.run_test() as pilot:
        kv = app.query_one("#providers", KeyValueEditor)
        kv.query_one(DataTable).focus()

        await pilot.press("e")
        await pilot.pause()

        assert kv.values() == {"work": str(new)}
        assert seen["location"] == old.parent


async def test_editor_edit_same_path_keeps_clean(
    providers_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "work.db"
    store.write_text("x")
    providers.save({"work": str(store)})
    _install_picker(monkeypatch, store)
    app = ProvidersApp()
    async with app.run_test() as pilot:
        kv = app.query_one("#providers", KeyValueEditor)
        kv.query_one(DataTable).focus()

        await pilot.press("e")
        await pilot.pause()

        assert kv.values() == {"work": str(store)}
        assert app.dirty is False


async def test_editor_deletes_after_confirmation(
    providers_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "work.db"
    store.write_text("x")
    providers.save({"work": str(store)})
    app = ProvidersApp()
    async with app.run_test() as pilot:
        kv = app.query_one("#providers", KeyValueEditor)
        kv.query_one(DataTable).focus()

        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()

        assert kv.values() == {}
        assert app.dirty is True


async def test_editor_quit_prompts_when_dirty(
    providers_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "work.db"
    store.write_text("x")
    _install_picker(monkeypatch, store)
    app = ProvidersApp()
    async with app.run_test() as pilot:
        kv = app.query_one("#providers", KeyValueEditor)
        kv.query_one(DataTable).focus()
        await pilot.press("a")
        await pilot.pause()
        modal = app.screen
        modal.query_one("#row-name", Input).value = "work"
        modal.query_one("#save", Button).press()
        await pilot.pause()
        assert app.dirty is True

        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ConfirmScreen)
        app.screen.query_one("#cancel", Button).press()
        await pilot.pause()
        assert app.is_running

        await pilot.press("ctrl+q")
        await pilot.pause()
        app.screen.query_one("#confirm", Button).press()
        await pilot.pause()
        assert app.is_running is False


async def test_editor_quit_clean_exits_without_prompt(
    providers_file: Path,
) -> None:
    app = ProvidersApp()
    async with app.run_test() as pilot:
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app.is_running is False


async def test_editor_warns_when_override_is_set(
    providers_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(providers.ENV_VAR, "work:/tmp/work.db")
    app = ProvidersApp()
    async with app.run_test():
        assert app.query_one("#override-notice", Static)


async def test_editor_ignores_malformed_file(
    providers_file: Path,
) -> None:
    providers_file.write_text("count = 3\n")
    app = ProvidersApp()
    async with app.run_test():
        kv = app.query_one("#providers", KeyValueEditor)
        assert kv.values() == {}
