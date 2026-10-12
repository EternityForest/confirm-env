"""Changing a store's passphrase and migrating its cipher format.

Re-encryption is done by exporting the whole store into a fresh database
with a new salt and a key derived from the new passphrase. Because the
export writes the installed SQLCipher's current format, the same operation
also migrates stores written by older SQLCipher builds (the
``cipher_compatibility`` recorded in the sidecar is dropped because it is
no longer needed).

A store is a *pair* of files (the database and its sidecar) that cannot be
renamed as a single atomic step. To keep the operation recoverable we:

1. Export the new database and the new sidecar next to the old ones,
   using ``.reencrypt`` temporary names.
2. Verify the new database opens with the new key.
3. Copy the old database to a ``.previous`` backup, so a consistent old
   pair survives even if the swap below is interrupted.
4. ``os.replace`` the new database and sidecar over the old ones.
5. Delete the backup.

If a step fails, :func:`reencrypt` rolls back and leaves the original
store untouched. If the process is killed outright, the next
:func:`recover` call restores a consistent pair without needing the
passphrase.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import replace
from pathlib import Path

from confirm_env import sidecar
from confirm_env.database import Database, UnlockError
from confirm_env.sidecar import CIPHER_COMPATIBILITY, Sidecar

#: Suffix for the in-progress re-encrypted database and sidecar.
TEMP_SUFFIX = ".reencrypt"

#: Suffix for the backup of the original database.
BACKUP_SUFFIX = ".previous"


class MigrationError(Exception):
    """Raised when a store cannot be re-encrypted."""


def _temp_db_path(store: Path) -> Path:
    return store.with_name(store.name + TEMP_SUFFIX)


def _temp_sidecar_path(store: Path) -> Path:
    path = sidecar.sidecar_path(store)
    return path.with_name(path.name + TEMP_SUFFIX)


def _backup_db_path(store: Path) -> Path:
    return store.with_name(store.name + BACKUP_SUFFIX)


def reencrypt(
    store: Path,
    *,
    old_key: bytes | None,
    old_compatibility: int | None,
    new_passphrase: str,
) -> Sidecar:
    """Re-encrypt ``store`` under ``new_passphrase`` in the current format.

    ``old_key`` is the raw key the store is currently protected with (or
    ``None`` for an unencrypted store). Returns the new sidecar; the caller
    must reopen the store with a key derived from ``new_passphrase``.
    """
    if not new_passphrase:
        raise MigrationError("a passphrase is required")
    if not store.is_file():
        raise MigrationError(f"no such store: {store}")
    recover(store)

    old_sidecar = sidecar.load(store)
    new_record = sidecar.create(new_passphrase)

    new_record = replace(
        new_record,
        lock_settings=old_sidecar.lock_settings,
        database_title=old_sidecar.database_title,
        database_description=old_sidecar.database_description,
    )

    assert new_record.salt is not None, "encrypted sidecar carries a salt"
    new_key = new_record.derive_key(new_passphrase)

    temp_db = _temp_db_path(store)
    temp_sidecar = _temp_sidecar_path(store)
    backup_db = _backup_db_path(store)

    source = Database.open(store, old_key, old_compatibility)
    try:
        source.export_to(temp_db, new_key)
    finally:
        source.close()

    try:
        _verify(temp_db, new_key)
        sidecar.write(temp_sidecar, new_record)
        _copy(store, backup_db)
        temp_db.replace(store)
        temp_sidecar.replace(sidecar.sidecar_path(store))
    except BaseException:
        _rollback(store)
        raise
    _remove(backup_db)
    sidecar.remove_legacy(store)
    return new_record


def recover(store: Path) -> None:
    """Finish or roll back an interrupted re-encryption.

    Safe to call at any time; it is a no-op when no temporary files are
    present. When the swap did not complete the original database is
    restored from its backup, so the old passphrase keeps working.
    """
    temp_db = _temp_db_path(store)
    temp_sidecar = _temp_sidecar_path(store)
    backup_db = _backup_db_path(store)
    if temp_sidecar.exists():
        # The sidecar swap did not happen, so the store is either still the
        # original or a freshly exported copy whose sidecar is missing.
        # Rolling back to the preserved original is safe either way.
        _rollback(store)
    else:
        # The swap completed; the backup is no longer needed.
        _remove(backup_db)
        _remove(temp_db)
    if sidecar.sidecar_path(store).is_file():
        # A completed migration wrote the TOML sidecar; drop the legacy
        # JSON one so the store never keeps two sidecars.
        sidecar.remove_legacy(store)


def _rollback(store: Path) -> None:
    backup_db = _backup_db_path(store)
    if backup_db.exists():
        backup_db.replace(store)
    _remove(_temp_db_path(store))
    _remove(_temp_sidecar_path(store))


def _verify(path: Path, key: bytes) -> None:
    try:
        probe = Database.open(path, key, CIPHER_COMPATIBILITY)
    except UnlockError as exc:
        raise MigrationError(
            "re-encrypted store failed to verify",
        ) from exc
    probe.close()


def _copy(source: Path, target: Path) -> None:
    data = source.read_bytes()
    with target.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _remove(path: Path) -> None:
    with suppress(FileNotFoundError):
        path.unlink()
