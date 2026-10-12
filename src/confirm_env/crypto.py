"""argon2id key derivation.

SQLCipher's built-in key derivation is a low-iteration PBKDF2 that is no
longer considered adequate. Instead of letting SQLCipher derive the key from
a passphrase, we derive a 256-bit key ourselves with argon2id and hand
SQLCipher that raw key. The salt and argon2id parameters therefore live in a
sidecar file next to the database (see :mod:`confirm_env.sidecar`) rather
than being buried inside the SQLCipher header.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw

#: Length of the derived key in bytes. 32 bytes (256 bits) is the largest
#: raw key SQLCipher accepts.
KEY_LENGTH = 32

#: Length of the random salt in bytes.
SALT_LENGTH = 16

#: Reasonable interactive argon2id defaults. They are stored in the sidecar
#: so that they can be raised later without breaking existing databases.
DEFAULT_TIME_COST = 3
DEFAULT_MEMORY_COST = 65536  # kibibytes (64 MiB)
DEFAULT_PARALLELISM = 4


@dataclass(frozen=True)
class KdfParams:
    """argon2id parameters persisted in the sidecar file."""

    time_cost: int = DEFAULT_TIME_COST
    memory_cost: int = DEFAULT_MEMORY_COST
    parallelism: int = DEFAULT_PARALLELISM
    hash_len: int = KEY_LENGTH

    def __post_init__(self) -> None:
        if self.time_cost < 1:
            raise ValueError("time_cost must be at least 1")
        if self.memory_cost < 8:
            raise ValueError("memory_cost must be at least 8 KiB")
        if self.parallelism < 1:
            raise ValueError("parallelism must be at least 1")
        if self.hash_len != KEY_LENGTH:
            raise ValueError(f"hash_len must be {KEY_LENGTH} bytes")


def new_salt() -> bytes:
    """Return a fresh cryptographically secure salt."""
    return os.urandom(SALT_LENGTH)


def derive_key(passphrase: str, salt: bytes, params: KdfParams) -> bytes:
    """Derive the raw database key for ``passphrase``.

    The result is deterministic for a given passphrase, salt and parameter
    set, so the same key is produced on every unlock.
    """
    if not salt:
        raise ValueError("salt must not be empty")
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
        hash_len=params.hash_len,
        type=Type.ID,
    )
