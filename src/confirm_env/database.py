"""Encrypted storage for environment variables and metadata.

The store is a SQLCipher database. When a raw key is supplied the database
is encrypted; when it is not (the "no passphrase" case) the store is an
ordinary SQLite database and SQLCipher is bypassed entirely.

Two tables are used:

``env``       the environment variables, ``key``/``value``.
``metadata``  free-form user metadata (notes, description, ...).

Application settings live in the sidecar file instead, because some of them
must be readable before the encrypted database is opened.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlcipher3 import dbapi2 as sqlcipher

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from sqlcipher3.dbapi2 import Connection

#: Environment variable names must be usable in a POSIX shell.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS env (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metadata (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_TABLES = frozenset({"env", "metadata"})


class UnlockError(Exception):
    """Raised when a database cannot be opened, e.g. a wrong passphrase."""


def is_valid_env_name(name: str) -> bool:
    """Whether ``name`` is a legal POSIX environment variable name."""
    return bool(_ENV_NAME_RE.match(name))


class Database:
    """A thin, typed wrapper around a confirm-env SQLCipher connection."""

    def __init__(self, connection: Connection, *, encrypted: bool) -> None:
        self._connection = connection
        self.encrypted = encrypted

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def create(cls, path: Path, key: bytes | None) -> Database:
        """Create a new database (and its schema) at ``path``.

        ``key`` is the raw 32-byte key, or ``None`` for an unencrypted
        store. The caller is responsible for writing the sidecar.
        """
        if path.exists() and path.stat().st_size > 0:
            raise FileExistsError(f"database already exists: {path}")
        connection = cls._connect(path, key)
        try:
            connection.executescript(_SCHEMA)
            connection.commit()
        except BaseException:
            connection.close()
            raise
        return cls(connection, encrypted=key is not None)

    @classmethod
    def open(cls, path: Path, key: bytes | None) -> Database:
        """Open an existing database, verifying the key if encrypted."""
        if not path.is_file():
            raise FileNotFoundError(f"no such database: {path}")
        return cls(cls._connect(path, key), encrypted=key is not None)

    @staticmethod
    def _connect(path: Path, key: bytes | None) -> Connection:
        try:
            connection = sqlcipher.connect(str(path))
        except sqlcipher.Error as exc:  # pragma: no cover - driver level
            raise UnlockError(str(exc)) from exc
        try:
            connection.row_factory = sqlcipher.Row
            if key is not None:
                if len(key) != 32:
                    raise ValueError("raw database key must be 32 bytes")
                connection.execute("PRAGMA cipher_page_size = 4096")
                connection.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
            connection.execute("PRAGMA busy_timeout = 5000")
            # Removable media and cheap flash: prefer durability to speed.
            connection.execute("PRAGMA synchronous = FULL")
            # Touch the schema; with a wrong key SQLCipher raises here.
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher.DatabaseError as exc:
            connection.close()
            raise UnlockError(
                "could not open database (wrong passphrase or corrupt file)",
            ) from exc
        except BaseException:
            connection.close()
            raise
        return connection

    def close(self) -> None:
        """Close the underlying connection (idempotent)."""
        self._connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- environment variables --------------------------------------------

    def list_env(self) -> dict[str, str]:
        """Return all environment variables as an ordered mapping."""
        return self._list("env")

    def get_env(self, name: str) -> str | None:
        return self._get("env", name)

    def set_env(self, name: str, value: str) -> None:
        if not is_valid_env_name(name):
            raise ValueError(f"invalid environment variable name: {name!r}")
        self._set("env", name, value)

    def delete_env(self, name: str) -> None:
        self._delete("env", name)

    def replace_env(self, variables: Mapping[str, str]) -> None:
        """Replace the whole env table atomically."""
        for name in variables:
            if not is_valid_env_name(name):
                raise ValueError(f"invalid environment variable name: {name!r}")
        self._replace("env", variables)

    # -- metadata ----------------------------------------------------------

    def list_metadata(self) -> dict[str, str]:
        return self._list("metadata")

    def set_metadata(self, name: str, value: str) -> None:
        self._set("metadata", name, value)

    def delete_metadata(self, name: str) -> None:
        self._delete("metadata", name)

    def replace_metadata(self, metadata: Mapping[str, str]) -> None:
        self._replace("metadata", metadata)

    # -- generic table helpers --------------------------------------------

    def _list(self, table: str) -> dict[str, str]:
        _check_table(table)
        cursor = self._connection.execute(
            f"SELECT key, value FROM {table} ORDER BY key",
        )
        return {row["key"]: row["value"] for row in cursor}

    def _get(self, table: str, key: str) -> str | None:
        _check_table(table)
        row = self._connection.execute(
            f"SELECT value FROM {table} WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else row["value"]

    def _set(self, table: str, key: str, value: str) -> None:
        _check_table(table)
        self._connection.execute(
            f"INSERT INTO {table} (key, value) VALUES (?, ?) "
            f"ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self._connection.commit()

    def _delete(self, table: str, key: str) -> None:
        _check_table(table)
        self._connection.execute(
            f"DELETE FROM {table} WHERE key = ?",
            (key,),
        )
        self._connection.commit()

    def _replace(self, table: str, mapping: Mapping[str, str]) -> None:
        _check_table(table)
        with self._connection:
            self._connection.execute(f"DELETE FROM {table}")
            self._connection.executemany(
                f"INSERT INTO {table} (key, value) VALUES (?, ?)",
                mapping.items(),
            )


def _check_table(table: str) -> None:
    if table not in _TABLES:
        raise ValueError(f"unknown table: {table!r}")
