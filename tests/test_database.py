"""Tests for the SQLCipher-backed store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from confirm_env.database import Database, UnlockError, is_valid_env_name

KEY = b"\x00" * 32
OTHER_KEY = b"\x01" * 32


def test_env_crud(tmp_path: Path) -> None:
    database = Database.create(tmp_path / "a.db", None)
    try:
        database.set_env("A", "1")
        database.set_env("B", "2")
        assert database.list_env() == {"A": "1", "B": "2"}
        database.set_env("A", "9")
        assert database.get_env("A") == "9"
        database.delete_env("A")
        assert database.get_env("A") is None
    finally:
        database.close()


def test_replace_env_replaces_everything(tmp_path: Path) -> None:
    database = Database.create(tmp_path / "a.db", None)
    try:
        database.set_env("OLD", "1")
        database.replace_env({"NEW": "2"})
        assert database.list_env() == {"NEW": "2"}
    finally:
        database.close()


@pytest.mark.parametrize("name", ["1BAD", "with-dash", "with space", ""])
def test_set_env_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    database = Database.create(tmp_path / "a.db", None)
    try:
        with pytest.raises(ValueError):
            database.set_env(name, "x")
        with pytest.raises(ValueError):
            database.replace_env({name: "x"})
    finally:
        database.close()


def test_is_valid_env_name() -> None:
    assert is_valid_env_name("ABC_1")
    assert is_valid_env_name("_private")
    assert not is_valid_env_name("1ABC")
    assert not is_valid_env_name("A-B")


def test_metadata_crud(tmp_path: Path) -> None:
    database = Database.create(tmp_path / "a.db", None)
    try:
        database.set_metadata("owner", "team")
        database.set_metadata("note", "hello")
        assert database.list_metadata() == {"note": "hello", "owner": "team"}
        database.delete_metadata("note")
        assert database.list_metadata() == {"owner": "team"}
        database.replace_metadata({})
        assert database.list_metadata() == {}
    finally:
        database.close()


def test_encrypted_roundtrip_and_wrong_key(tmp_path: Path) -> None:
    path = tmp_path / "e.db"
    database = Database.create(path, KEY)
    database.set_env("SECRET", "value")
    database.close()

    with pytest.raises(UnlockError):
        Database.open(path, OTHER_KEY)

    reopened = Database.open(path, KEY)
    try:
        assert reopened.encrypted
        assert reopened.get_env("SECRET") == "value"
    finally:
        reopened.close()


def test_plaintext_is_readable_as_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "p.db"
    database = Database.create(path, None)
    database.set_env("A", "1")
    database.close()
    raw = sqlite3.connect(path)
    try:
        row = raw.execute("SELECT value FROM env WHERE key = 'A'").fetchone()
        assert row == ("1",)
    finally:
        raw.close()


def test_encrypted_is_not_readable_as_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "e.db"
    Database.create(path, KEY).close()
    raw = sqlite3.connect(path)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute("SELECT * FROM env").fetchall()
    finally:
        raw.close()


def test_create_existing_raises(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    Database.create(path, None).close()
    with pytest.raises(FileExistsError):
        Database.create(path, None)


def test_create_rejects_bad_key_length(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Database.create(tmp_path / "a.db", b"short")
