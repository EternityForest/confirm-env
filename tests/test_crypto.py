"""Tests for argon2id key derivation."""

from __future__ import annotations

import pytest

from confirm_env.crypto import (
    KEY_LENGTH,
    SALT_LENGTH,
    KdfParams,
    derive_key,
    new_salt,
)

#: Cheap parameters so the test suite stays fast.
CHEAP = KdfParams(time_cost=1, memory_cost=8, parallelism=1)


def test_new_salt_has_fixed_length_and_varies() -> None:
    first = new_salt()
    second = new_salt()
    assert len(first) == SALT_LENGTH
    assert first != second


def test_derive_key_is_deterministic_and_sized() -> None:
    salt = new_salt()
    first = derive_key("pw", salt, CHEAP)
    second = derive_key("pw", salt, CHEAP)
    assert first == second
    assert len(first) == KEY_LENGTH


def test_derive_key_depends_on_passphrase_salt_and_params() -> None:
    salt_a = b"a" * SALT_LENGTH
    salt_b = b"b" * SALT_LENGTH
    assert derive_key("pw", salt_a, CHEAP) != derive_key("other", salt_a, CHEAP)
    assert derive_key("pw", salt_a, CHEAP) != derive_key("pw", salt_b, CHEAP)
    stronger = KdfParams(time_cost=2, memory_cost=8, parallelism=1)
    assert derive_key("pw", salt_a, CHEAP) != derive_key("pw", salt_a, stronger)


def test_derive_key_rejects_empty_salt() -> None:
    with pytest.raises(ValueError):
        derive_key("pw", b"", CHEAP)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"time_cost": 0},
        {"memory_cost": 4},
        {"parallelism": 0},
        {"hash_len": 16},
    ],
)
def test_invalid_kdf_params_rejected(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        KdfParams(**kwargs)
