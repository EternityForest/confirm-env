"""Tests for passphrase changes and cipher-format migration."""

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from confirm_env import migrate, sidecar
from confirm_env.database import Database, UnlockError
from confirm_env.sidecar import CIPHER_COMPATIBILITY, Sidecar

OLD_PASSPHRASE = "old passphrase"
NEW_PASSPHRASE = "new passphrase"


def _make_v3_store(tmp_path: Path) -> tuple[Path, bytes, Sidecar]:
    """A legacy (SQLCipher 3 format) encrypted store with two tables."""
    store = tmp_path / "legacy.db"
    record = replace(
        sidecar.create(OLD_PASSPHRASE),
        cipher_compatibility=3,
    )
    key = record.derive_key(OLD_PASSPHRASE)
    database = Database.create(store, key, 3)
    database.set_env("FOO", "bar")
    database.set_metadata("note", "hello")
    database.close()
    sidecar.save(store, record)
    return store, key, record


def _make_current_store(tmp_path: Path) -> tuple[Path, bytes, Sidecar]:
    """A current-format encrypted store."""
    store = tmp_path / "current.db"
    record = sidecar.create(OLD_PASSPHRASE)
    key = record.derive_key(OLD_PASSPHRASE)
    database = Database.create(store, key, record.cipher_compatibility)
    database.set_env("FOO", "bar")
    database.close()
    sidecar.save(store, record)
    return store, key, record


def test_reencrypt_migrates_v3_and_changes_passphrase(tmp_path: Path) -> None:
    store, old_key, old_record = _make_v3_store(tmp_path)

    new_record = migrate.reencrypt(
        store,
        old_key=old_key,
        old_compatibility=3,
        new_passphrase=NEW_PASSPHRASE,
    )

    assert new_record.cipher_compatibility == CIPHER_COMPATIBILITY
    assert new_record.salt != old_record.salt
    new_key = new_record.derive_key(NEW_PASSPHRASE)
    reopened = Database.open(store, new_key, CIPHER_COMPATIBILITY)
    try:
        assert reopened.get_env("FOO") == "bar"
        assert reopened.list_metadata()["note"] == "hello"
    finally:
        reopened.close()

    # The store is now v4, so the legacy format no longer reads it and the
    # old passphrase no longer works.
    with pytest.raises(UnlockError):
        Database.open(store, new_key, 3)
    with pytest.raises(UnlockError):
        Database.open(store, old_key, CIPHER_COMPATIBILITY)

    loaded = sidecar.load(store)
    assert loaded.cipher_compatibility == CIPHER_COMPATIBILITY
    assert loaded.salt == new_record.salt


def test_reencrypt_changes_passphrase_on_current_store(tmp_path: Path) -> None:
    store, old_key, old_record = _make_current_store(tmp_path)

    new_record = migrate.reencrypt(
        store,
        old_key=old_key,
        old_compatibility=old_record.cipher_compatibility,
        new_passphrase=NEW_PASSPHRASE,
    )

    new_key = new_record.derive_key(NEW_PASSPHRASE)
    reopened = Database.open(store, new_key, CIPHER_COMPATIBILITY)
    try:
        assert reopened.get_env("FOO") == "bar"
    finally:
        reopened.close()
    with pytest.raises(UnlockError):
        Database.open(store, old_key, CIPHER_COMPATIBILITY)


def test_reencrypt_adds_passphrase_to_plain_store(tmp_path: Path) -> None:
    store = tmp_path / "plain.db"
    record = sidecar.create(None)
    database = Database.create(store, None)
    database.set_env("FOO", "bar")
    database.close()
    sidecar.save(store, record)

    new_record = migrate.reencrypt(
        store,
        old_key=None,
        old_compatibility=None,
        new_passphrase=NEW_PASSPHRASE,
    )

    assert new_record.encrypted
    assert sidecar.load(store).encrypted
    new_key = new_record.derive_key(NEW_PASSPHRASE)
    reopened = Database.open(store, new_key, CIPHER_COMPATIBILITY)
    try:
        assert reopened.get_env("FOO") == "bar"
    finally:
        reopened.close()


def test_reencrypt_rejects_empty_passphrase(tmp_path: Path) -> None:
    store, old_key, _ = _make_v3_store(tmp_path)
    with pytest.raises(migrate.MigrationError):
        migrate.reencrypt(
            store,
            old_key=old_key,
            old_compatibility=3,
            new_passphrase="",
        )


def test_reencrypt_rejects_missing_store(tmp_path: Path) -> None:
    with pytest.raises(migrate.MigrationError):
        migrate.reencrypt(
            tmp_path / "nope.db",
            old_key=None,
            old_compatibility=None,
            new_passphrase=NEW_PASSPHRASE,
        )


def test_reencrypt_failure_leaves_store_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, old_key, old_record = _make_v3_store(tmp_path)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(sidecar, "write", boom)
    with pytest.raises(OSError):
        migrate.reencrypt(
            store,
            old_key=old_key,
            old_compatibility=3,
            new_passphrase=NEW_PASSPHRASE,
        )
    monkeypatch.undo()

    reopened = Database.open(store, old_key, 3)
    try:
        assert reopened.get_env("FOO") == "bar"
    finally:
        reopened.close()
    assert sidecar.load(store).salt == old_record.salt
    assert not (tmp_path / "legacy.db.reencrypt").exists()
    assert not (tmp_path / "legacy.db.sidecar.json.reencrypt").exists()
    assert not (tmp_path / "legacy.db.previous").exists()


def test_recover_rolls_back_incomplete_swap(tmp_path: Path) -> None:
    store, old_key, old_record = _make_v3_store(tmp_path)
    new_record = replace(
        sidecar.create(NEW_PASSPHRASE),
        cipher_compatibility=CIPHER_COMPATIBILITY,
    )
    new_key = new_record.derive_key(NEW_PASSPHRASE)

    # Reproduce the state after the database swap but before the sidecar
    # swap: the store is the new file, the sidecar is still the old one,
    # and the original database survives as the backup.
    source = Database.open(store, old_key, 3)
    try:
        source.export_to(tmp_path / "legacy.db.reencrypt", new_key)
    finally:
        source.close()
    sidecar.write(tmp_path / "legacy.db.sidecar.json.reencrypt", new_record)
    shutil.copy(store, tmp_path / "legacy.db.previous")
    (tmp_path / "legacy.db.reencrypt").replace(store)

    migrate.recover(store)

    reopened = Database.open(store, old_key, 3)
    try:
        assert reopened.get_env("FOO") == "bar"
    finally:
        reopened.close()
    assert sidecar.load(store).salt == old_record.salt
    assert not (tmp_path / "legacy.db.reencrypt").exists()
    assert not (tmp_path / "legacy.db.sidecar.json.reencrypt").exists()
    assert not (tmp_path / "legacy.db.previous").exists()


def test_recover_cleans_backup_after_complete_swap(tmp_path: Path) -> None:
    store, old_key, _ = _make_v3_store(tmp_path)
    new_record = migrate.reencrypt(
        store,
        old_key=old_key,
        old_compatibility=3,
        new_passphrase=NEW_PASSPHRASE,
    )
    # Simulate a crash after the swap but before the backup was removed.
    (tmp_path / "legacy.db.previous").write_bytes(b"stale")

    migrate.recover(store)

    assert not (tmp_path / "legacy.db.previous").exists()
    new_key = new_record.derive_key(NEW_PASSPHRASE)
    reopened = Database.open(store, new_key, CIPHER_COMPATIBILITY)
    try:
        assert reopened.get_env("FOO") == "bar"
    finally:
        reopened.close()


def test_recover_is_noop_when_clean(tmp_path: Path) -> None:
    store, old_key, _ = _make_v3_store(tmp_path)
    migrate.recover(store)
    reopened = Database.open(store, old_key, 3)
    try:
        assert reopened.get_env("FOO") == "bar"
    finally:
        reopened.close()
