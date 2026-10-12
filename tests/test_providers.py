"""Tests for the provider mapping file."""

from __future__ import annotations

from pathlib import Path

import pytest

from confirm_env import providers


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


def test_config_dir_is_absolute() -> None:
    assert providers.config_dir().is_absolute()


def test_load_missing_returns_empty(providers_file: Path) -> None:
    assert providers.load() == {}


def test_save_load_roundtrip(providers_file: Path) -> None:
    providers.save({"work": "/srv/work.db"})
    assert providers.load() == {"work": "/srv/work.db"}


def test_load_rejects_non_string_value(providers_file: Path) -> None:
    providers_file.write_text("count = 3\n")
    with pytest.raises(ValueError):
        providers.load()


def test_set_provider_requires_existing_file(
    providers_file: Path,
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        providers.set_provider("work", tmp_path / "missing.db")


def test_set_provider_rejects_empty_name(
    providers_file: Path,
    tmp_path: Path,
) -> None:
    store = tmp_path / "work.db"
    store.write_text("x")
    with pytest.raises(ValueError):
        providers.set_provider("  ", store)


def test_set_provider_persists(
    providers_file: Path,
    tmp_path: Path,
) -> None:
    store = tmp_path / "work.db"
    store.write_text("x")
    providers.set_provider("work", store)
    assert providers.lookup("work") == store


def test_lookup_unknown_returns_none(providers_file: Path) -> None:
    assert providers.lookup("nope") is None


def test_lookup_uses_env_override(
    providers_file: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers.save({"work": "/srv/work.db"})
    store = tmp_path / "override.db"
    monkeypatch.setenv(providers.ENV_VAR, f"work:{store}")
    assert providers.lookup("work") == store


def test_env_override_bypasses_file(
    providers_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers.save({"work": "/srv/work.db"})
    monkeypatch.setenv(providers.ENV_VAR, "other:/tmp/other.db")
    assert providers.lookup("work") is None
    assert providers.lookup("other") == Path("/tmp/other.db")


def test_parse_env_multiple_entries() -> None:
    assert providers.parse_env("a:/x,b:/y") == {"a": "/x", "b": "/y"}


def test_parse_env_keeps_windows_drive() -> None:
    assert providers.parse_env("a:C:\\store.db") == {"a": "C:\\store.db"}


def test_parse_env_empty_is_empty() -> None:
    assert providers.parse_env("") == {}


@pytest.mark.parametrize("value", ["no-colon", ":missing-name", "name:"])
def test_parse_env_rejects_invalid(value: str) -> None:
    with pytest.raises(ValueError):
        providers.parse_env(value)
