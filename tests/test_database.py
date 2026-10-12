"""Tests for the SQLCipher-backed store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from confirm_env import log
from confirm_env.database import Database, UnlockError, is_valid_env_name

KEY = b"\x00" * 32
OTHER_KEY = b"\x01" * 32


def _entry(command: str) -> log.LogEntry:
    return log.LogEntry(
        timestamp="2026-01-01T00:00:00+00:00",
        command=command,
        username="tester",
        workdir="/tmp",
        hostname="host",
    )


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


def test_open_uses_recorded_cipher_compatibility(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    database = Database.create(path, KEY, cipher_compatibility=3)
    database.set_env("SECRET", "value")
    database.close()

    # An explicitly recorded compatibility opens directly.
    reopened = Database.open(path, KEY, 3)
    try:
        assert reopened.get_env("SECRET") == "value"
    finally:
        reopened.close()


def test_open_probes_legacy_format_when_unrecorded(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    database = Database.create(path, KEY, cipher_compatibility=3)
    database.set_env("SECRET", "value")
    database.close()

    # No recorded compatibility: the native format fails and the legacy
    # SQLCipher 3 format is probed.
    reopened = Database.open(path, KEY)
    try:
        assert reopened.get_env("SECRET") == "value"
    finally:
        reopened.close()


def test_explicit_compatibility_mismatch_raises(tmp_path: Path) -> None:
    path = tmp_path / "v4.db"
    Database.create(path, KEY, cipher_compatibility=4).close()
    with pytest.raises(UnlockError):
        Database.open(path, KEY, 3)


def test_wrong_key_fails_through_compatibility_fallback(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.db"
    Database.create(path, KEY, cipher_compatibility=3).close()
    with pytest.raises(UnlockError):
        Database.open(path, OTHER_KEY)


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


def test_append_and_list_log(tmp_path: Path) -> None:
    database = Database.create(tmp_path / "a.db", None)
    try:
        database.append_log(_entry("one"))
        database.append_log(_entry("two"))
        assert database.count_log() == 2
        page = database.log_page(0, 10)
        assert [entry.command for entry in page] == ["two", "one"]
        assert page[0].username == "tester"
        assert page[0].hostname == "host"
        assert page[0].workdir == "/tmp"
    finally:
        database.close()


def test_log_page_paginates(tmp_path: Path) -> None:
    database = Database.create(tmp_path / "a.db", None)
    try:
        for index in range(5):
            database.append_log(_entry(f"cmd-{index}"))
        first = database.log_page(0, 2)
        second = database.log_page(2, 2)
        assert [entry.command for entry in first] == ["cmd-4", "cmd-3"]
        assert [entry.command for entry in second] == ["cmd-2", "cmd-1"]
    finally:
        database.close()


@pytest.mark.parametrize(("offset", "limit"), [(-1, 10), (0, 0)])
def test_log_page_rejects_bad_arguments(
    tmp_path: Path,
    offset: int,
    limit: int,
) -> None:
    database = Database.create(tmp_path / "a.db", None)
    try:
        with pytest.raises(ValueError):
            database.log_page(offset, limit)
    finally:
        database.close()


def test_append_log_trims_oldest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(log, "MAX_ENTRIES", 5)
    monkeypatch.setattr(log, "TRIM_ENTRIES", 2)
    database = Database.create(tmp_path / "a.db", None)
    try:
        for index in range(5):
            database.append_log(_entry(f"cmd-{index}"))
        assert database.count_log() == 5
        database.append_log(_entry("cmd-5"))
        assert database.count_log() == 4
        remaining = database.log_page(0, 10)
        assert [entry.command for entry in remaining] == [
            "cmd-5",
            "cmd-4",
            "cmd-3",
            "cmd-2",
        ]
    finally:
        database.close()


def test_log_survives_encrypted_reopen(tmp_path: Path) -> None:
    path = tmp_path / "e.db"
    database = Database.create(path, KEY)
    database.append_log(_entry("secret-cmd"))
    database.close()

    reopened = Database.open(path, KEY)
    try:
        assert [entry.command for entry in reopened.log_page(0, 10)] == [
            "secret-cmd",
        ]
    finally:
        reopened.close()


def test_open_adds_missing_log_table(tmp_path: Path) -> None:
    # A store written before logging existed has no ``log`` table.
    path = tmp_path / "old.db"
    Database.create(path, None).close()
    raw = sqlite3.connect(path)
    try:
        raw.execute("DROP TABLE log")
        raw.commit()
    finally:
        raw.close()

    reopened = Database.open(path, None)
    try:
        reopened.append_log(_entry("after-upgrade"))
        assert reopened.count_log() == 1
    finally:
        reopened.close()
