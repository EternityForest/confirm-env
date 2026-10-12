"""The sidecar KDF file that accompanies every confirm-env database.

A confirm-env store is a pair of files:

* ``FILE``                the SQLCipher (or plain SQLite) database.
* ``FILE.sidecar.toml``   the salt and argon2id parameters needed to
                          derive the database key from a passphrase.

The salt is *not* secret; keeping it in a plain file next to the database
lets us use a modern KDF without having to read the encrypted header, and
keeps the format inspectable and version-controllable. Keeping it in a
separate file also means a database can be created and moved without ever
calling into SQLCipher's legacy key derivation.

The sidecar used to be JSON (``FILE.sidecar.json``). We still *read* that
legacy format so existing stores keep working, and delete it once the
store is next written as TOML, so a store never has two sidecars.
"""

from __future__ import annotations

import base64
import json
import tomllib
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import tomli_w

from confirm_env.atomicwrite import atomic_write_text
from confirm_env.crypto import KdfParams, derive_key, new_salt

#: Marker written into the sidecar so a stray ``*.sidecar.toml`` is
#: recognisable.
FORMAT = "confirm-env"

#: Schema version of the sidecar file itself.
VERSION = 1

#: Suffix appended to the database path to build the sidecar path.
SIDECAR_SUFFIX = ".sidecar.toml"

#: Suffix of the pre-TOML JSON sidecar, still read for old stores.
LEGACY_SIDECAR_SUFFIX = ".sidecar.json"


#: Default unlock delay, in seconds.
DEFAULT_UNLOCK_DELAY = 5.0

#: The SQLCipher ``cipher_compatibility`` pragma value used for new stores.
#: Four is SQLCipher's current format; recording it lets us keep reading
#: stores written by older SQLCipher 3.x builds via the pragma.
CIPHER_COMPATIBILITY = 4


class EncryptionMode(StrEnum):
    """How (or whether) the database is encrypted."""

    SQLCIPHER = "sqlcipher"
    NONE = "none"


@dataclass()
class LockSettings:
    """User-configurable settings.

    Settings live in the sidecar rather than the database because some of
    them (the unlock delay, the math lock) must be known *before* the
    encrypted database can be opened.
    """

    unlock_delay: float = DEFAULT_UNLOCK_DELAY
    math_lock_digits: int = 0

    def __post_init__(self) -> None:
        if self.unlock_delay < 0:
            raise ValueError("unlock delay must not be negative")
        if isinstance(self.math_lock_digits, bool) or not isinstance(
            self.math_lock_digits,
            int,
        ):
            raise ValueError("math lock digits must be an integer")
        if self.math_lock_digits < 0:
            raise ValueError("math lock digits must not be negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "unlock_delay": self.unlock_delay,
            "math_lock_digits": self.math_lock_digits,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LockSettings:
        raw = data.get("unlock_delay", DEFAULT_UNLOCK_DELAY)
        try:
            delay = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid unlock_delay setting") from exc
        digits = _parse_math_lock_digits(data.get("math_lock_digits", 0))
        return cls(unlock_delay=delay, math_lock_digits=digits)


@dataclass()
class Sidecar:
    """Parsed contents of a sidecar file."""

    encryption: EncryptionMode
    salt: bytes | None
    kdf: KdfParams | None
    cipher_compatibility: int | None = None
    lock_settings: LockSettings = field(default_factory=LockSettings)
    database_title: str = ""
    database_description: str = ""
    version: int = VERSION

    @property
    def encrypted(self) -> bool:
        """Whether the database is protected by SQLCipher."""
        return self.encryption is EncryptionMode.SQLCIPHER

    def derive_key(self, passphrase: str) -> bytes:
        """Derive the raw database key for ``passphrase``."""
        if not self.encrypted:
            raise ValueError("database is not encrypted")
        assert self.salt is not None, "encrypted sidecar must carry a salt"
        assert self.kdf is not None, "encrypted sidecar must carry KDF params"
        return derive_key(passphrase, self.salt, self.kdf)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a TOML-compatible dictionary.

        Top-level scalars are emitted before the nested ``lock_settings``
        and ``kdf`` tables because TOML requires a table's own keys to
        precede any of its sub-tables.
        """
        data: dict[str, Any] = {
            "format": FORMAT,
            "version": self.version,
            "encryption": self.encryption.value,
            "database_title": self.database_title,
            "database_description": self.database_description,
        }
        if self.encrypted:
            assert self.salt is not None and self.kdf is not None
            data["salt"] = base64.b64encode(self.salt).decode("ascii")
            if self.cipher_compatibility is not None:
                data["cipher_compatibility"] = self.cipher_compatibility
        data["lock_settings"] = self.lock_settings.to_dict()
        if self.encrypted:
            assert self.kdf is not None
            data["kdf"] = {
                "algorithm": "argon2id",
                "time_cost": self.kdf.time_cost,
                "memory_cost": self.kdf.memory_cost,
                "parallelism": self.kdf.parallelism,
                "hash_len": self.kdf.hash_len,
            }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Sidecar:
        """Parse and validate a dictionary previously written by us."""
        if data.get("format") != FORMAT:
            raise ValueError("not a confirm-env sidecar file")
        version = data.get("version")
        if version != VERSION:
            raise ValueError(f"unsupported sidecar version: {version!r}")
        try:
            encryption = EncryptionMode(data["encryption"])
        except (KeyError, ValueError) as exc:
            raise ValueError("invalid encryption mode") from exc
        lock_settings = LockSettings.from_dict(data.get("lock_settings", {}))
        database_title = _parse_text(data, "database_title")
        database_description = _parse_text(data, "database_description")
        if encryption is EncryptionMode.NONE:
            return cls(
                encryption=EncryptionMode.NONE,
                salt=None,
                kdf=None,
                lock_settings=lock_settings,
                database_title=database_title,
                database_description=database_description,
                version=version,
            )
        try:
            salt = base64.b64decode(data["salt"], validate=True)
            raw_kdf = data["kdf"]
            kdf = KdfParams(
                time_cost=int(raw_kdf["time_cost"]),
                memory_cost=int(raw_kdf["memory_cost"]),
                parallelism=int(raw_kdf["parallelism"]),
                hash_len=int(raw_kdf["hash_len"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid KDF parameters") from exc
        cipher_compatibility = _parse_cipher_compatibility(data)
        return cls(
            encryption=EncryptionMode.SQLCIPHER,
            salt=salt,
            kdf=kdf,
            cipher_compatibility=cipher_compatibility,
            lock_settings=lock_settings,
            database_title=database_title,
            database_description=database_description,
            version=version,
        )


def _parse_cipher_compatibility(data: dict[str, Any]) -> int | None:
    """Read the optional ``cipher_compatibility`` field.

    A missing field means the store predates cipher-format tracking; the
    caller falls back to probing older SQLCipher formats.
    """
    raw = data.get("cipher_compatibility")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError("invalid cipher_compatibility")
    return raw


def _parse_math_lock_digits(raw: Any) -> int:
    """Read the optional ``math_lock_digits`` setting.

    A missing value means no math lock; zero disables it too.
    """
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError("invalid math_lock_digits setting")
    return raw


def _parse_text(data: dict[str, Any], key: str) -> str:
    """Read an optional free-text field, defaulting to an empty string."""
    raw = data.get(key, "")
    if not isinstance(raw, str):
        raise ValueError(f"invalid {key}")
    return raw


def create(passphrase: str | None) -> Sidecar:
    """Build a fresh sidecar for a new database.

    An empty or missing passphrase produces an unencrypted database.
    """
    if passphrase:
        return Sidecar(
            EncryptionMode.SQLCIPHER,
            new_salt(),
            KdfParams(),
            cipher_compatibility=CIPHER_COMPATIBILITY,
        )
    return Sidecar(EncryptionMode.NONE, None, None)


def sidecar_path(db_path: Path) -> Path:
    """Return the canonical (TOML) sidecar path for ``db_path``."""
    return db_path.with_name(db_path.name + SIDECAR_SUFFIX)


def legacy_sidecar_path(db_path: Path) -> Path:
    """Return the pre-TOML JSON sidecar path for ``db_path``."""
    return db_path.with_name(db_path.name + LEGACY_SIDECAR_SUFFIX)


def exists(db_path: Path) -> bool:
    """Whether a sidecar file exists for ``db_path``."""
    return (
        sidecar_path(db_path).is_file()
        or legacy_sidecar_path(db_path).is_file()
    )


def load(db_path: Path) -> Sidecar:
    """Read and validate the sidecar for ``db_path``.

    Prefers the TOML sidecar and falls back to the pre-TOML JSON sidecar
    so stores created by older versions keep working.
    """
    path = sidecar_path(db_path)
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    else:
        legacy = legacy_sidecar_path(db_path)
        if not legacy.is_file():
            raise FileNotFoundError(f"no sidecar file for {db_path}")
        data = json.loads(legacy.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("sidecar file must contain a table")
    return Sidecar.from_dict(data)


def save(db_path: Path, sidecar: Sidecar) -> None:
    """Atomically write ``sidecar`` next to ``db_path``.

    Any pre-TOML JSON sidecar is removed once the TOML file is safely in
    place, so a store never keeps two sidecars.
    """
    write(sidecar_path(db_path), sidecar)
    remove_legacy(db_path)


def write(path: Path, sidecar: Sidecar) -> None:
    """Atomically write ``sidecar`` to ``path`` as TOML."""
    payload = tomli_w.dumps(sidecar.to_dict())
    atomic_write_text(path, payload + "\n")


def remove_legacy(db_path: Path) -> None:
    """Delete a pre-TOML JSON sidecar for ``db_path`` if one is present."""
    with suppress(FileNotFoundError):
        legacy_sidecar_path(db_path).unlink()
