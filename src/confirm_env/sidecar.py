"""The sidecar KDF file that accompanies every confirm-env database.

A confirm-env store is a pair of files:

* ``FILE``                the SQLCipher (or plain SQLite) database.
* ``FILE.sidecar.json``   the salt and argon2id parameters needed to derive
                          the database key from a passphrase.

The salt is *not* secret; keeping it in a plain file next to the database
lets us use a modern KDF without having to read the encrypted header, and
keeps the format inspectable and version-controllable. Keeping it in a
separate file also means a database can be created and moved without ever
calling into SQLCipher's legacy key derivation.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from confirm_env.crypto import KdfParams, derive_key, new_salt

#: Marker written into the sidecar so a stray ``*.sidecar.json`` is
#: recognisable.
FORMAT = "confirm-env"

#: Schema version of the sidecar file itself.
VERSION = 1

#: Suffix appended to the database path to build the sidecar path.
SIDECAR_SUFFIX = ".sidecar.json"


#: Default unlock delay, in seconds.
DEFAULT_UNLOCK_DELAY = 5.0


class EncryptionMode(StrEnum):
    """How (or whether) the database is encrypted."""

    SQLCIPHER = "sqlcipher"
    NONE = "none"


@dataclass(frozen=True)
class Settings:
    """User-configurable settings.

    Settings live in the sidecar rather than the database because some of
    them (the unlock delay) must be known *before* the encrypted database
    can be opened.
    """

    unlock_delay: float = DEFAULT_UNLOCK_DELAY

    def __post_init__(self) -> None:
        if self.unlock_delay < 0:
            raise ValueError("unlock delay must not be negative")

    def to_json(self) -> dict[str, Any]:
        return {"unlock_delay": self.unlock_delay}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Settings:
        raw = data.get("unlock_delay", DEFAULT_UNLOCK_DELAY)
        try:
            return cls(float(raw))
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid unlock_delay setting") from exc


@dataclass(frozen=True)
class Sidecar:
    """Parsed contents of a sidecar file."""

    encryption: EncryptionMode
    salt: bytes | None
    kdf: KdfParams | None
    settings: Settings = field(default_factory=Settings)
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

    def to_json(self) -> dict[str, Any]:
        """Serialise to a plain JSON-compatible dictionary."""
        data: dict[str, Any] = {
            "format": FORMAT,
            "version": self.version,
            "encryption": self.encryption.value,
            "settings": self.settings.to_json(),
        }
        if self.encrypted:
            assert self.salt is not None and self.kdf is not None
            data["salt"] = base64.b64encode(self.salt).decode("ascii")
            data["kdf"] = {
                "algorithm": "argon2id",
                "time_cost": self.kdf.time_cost,
                "memory_cost": self.kdf.memory_cost,
                "parallelism": self.kdf.parallelism,
                "hash_len": self.kdf.hash_len,
            }
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Sidecar:
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
        settings = Settings.from_json(data.get("settings", {}))
        if encryption is EncryptionMode.NONE:
            return cls(
                encryption=EncryptionMode.NONE,
                salt=None,
                kdf=None,
                settings=settings,
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
        return cls(
            encryption=EncryptionMode.SQLCIPHER,
            salt=salt,
            kdf=kdf,
            settings=settings,
            version=version,
        )


def create(passphrase: str | None) -> Sidecar:
    """Build a fresh sidecar for a new database.

    An empty or missing passphrase produces an unencrypted database.
    """
    if passphrase:
        return Sidecar(EncryptionMode.SQLCIPHER, new_salt(), KdfParams())
    return Sidecar(EncryptionMode.NONE, None, None)


def sidecar_path(db_path: Path) -> Path:
    """Return the sidecar path for ``db_path``."""
    return db_path.with_name(db_path.name + SIDECAR_SUFFIX)


def exists(db_path: Path) -> bool:
    """Whether a sidecar file exists for ``db_path``."""
    return sidecar_path(db_path).is_file()


def load(db_path: Path) -> Sidecar:
    """Read and validate the sidecar for ``db_path``."""
    path = sidecar_path(db_path)
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("sidecar file must contain a JSON object")
    return Sidecar.from_json(data)


def save(db_path: Path, sidecar: Sidecar) -> None:
    """Atomically write ``sidecar`` next to ``db_path``.

    Writes go to a temporary file in the same directory, are flushed and
    fsynced, then renamed over the target. This keeps the sidecar intact if
    the device is unplugged or loses power mid-write.
    """
    if not db_path.parent.is_dir():
        raise FileNotFoundError(
            f"directory does not exist: {db_path.parent}",
        )
    path = sidecar_path(db_path)
    payload = json.dumps(sidecar.to_json(), indent=2, sort_keys=True) + "\n"
    _atomic_write_text(path, payload)


def _atomic_write_text(path: Path, text: str) -> None:
    handle_fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        tmp_path.replace(path)
        _fsync_directory(path.parent)
    except BaseException:
        with suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def _fsync_directory(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
