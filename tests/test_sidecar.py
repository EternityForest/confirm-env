"""Tests for the sidecar KDF file."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from confirm_env import sidecar
from confirm_env.crypto import derive_key
from confirm_env.sidecar import EncryptionMode, LockSettings


def test_create_encrypted() -> None:
    record = sidecar.create("pw")
    assert record.encrypted
    assert record.encryption is EncryptionMode.SQLCIPHER
    assert record.salt is not None and record.kdf is not None
    assert record.cipher_compatibility == sidecar.CIPHER_COMPATIBILITY


@pytest.mark.parametrize("empty", [None, ""])
def test_create_plaintext(empty: str | None) -> None:
    record = sidecar.create(empty)
    assert not record.encrypted
    assert record.encryption is EncryptionMode.NONE
    assert record.salt is None and record.kdf is None
    assert record.cipher_compatibility is None


def test_sidecar_path_suffix() -> None:
    assert sidecar.sidecar_path(Path("/tmp/a.db")).name == "a.db.sidecar.json"


def test_save_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    original = sidecar.create("pw")
    sidecar.save(path, original)
    loaded = sidecar.load(path)
    assert loaded.encrypted == original.encrypted
    assert loaded.salt == original.salt
    assert loaded.kdf == original.kdf
    assert loaded.cipher_compatibility == original.cipher_compatibility
    assert loaded.lock_settings == original.lock_settings


def test_load_legacy_sidecar_without_cipher_compatibility(
    tmp_path: Path,
) -> None:
    path = tmp_path / "s.db"
    payload = sidecar.create("pw").to_json()
    del payload["cipher_compatibility"]
    sidecar.sidecar_path(path).write_text(json.dumps(payload))
    assert sidecar.load(path).cipher_compatibility is None


@pytest.mark.parametrize("value", ["4", 0, -1, True, 2.0])
def test_load_rejects_invalid_cipher_compatibility(
    tmp_path: Path,
    value: object,
) -> None:
    path = tmp_path / "s.db"
    payload = sidecar.create("pw").to_json()
    payload["cipher_compatibility"] = value
    sidecar.sidecar_path(path).write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        sidecar.load(path)


def test_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    original = replace(
        sidecar.create(None),
        settings=LockSettings(unlock_delay=2.5),
    )
    sidecar.save(path, original)
    assert sidecar.load(path).lock_settings.unlock_delay == 2.5


def test_settings_math_lock_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    original = replace(
        sidecar.create(None),
        settings=LockSettings(unlock_delay=1.0, math_lock_digits=3),
    )
    sidecar.save(path, original)
    assert sidecar.load(path).lock_settings.math_lock_digits == 3


def test_load_legacy_sidecar_without_math_lock(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    payload = sidecar.create("pw").to_json()
    del payload["lock_settings"]["math_lock_digits"]
    sidecar.sidecar_path(path).write_text(json.dumps(payload))
    assert sidecar.load(path).lock_settings.math_lock_digits == 0


@pytest.mark.parametrize("value", ["3", -1, True, 2.5])
def test_load_rejects_invalid_math_lock_digits(
    tmp_path: Path,
    value: object,
) -> None:
    path = tmp_path / "s.db"
    payload = sidecar.create("pw").to_json()
    payload["settings"]["math_lock_digits"] = value
    sidecar.sidecar_path(path).write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        sidecar.load(path)


@pytest.mark.parametrize("value", [-1, True, 2.5])
def test_settings_reject_invalid_math_lock_digits(value: object) -> None:
    with pytest.raises(ValueError):
        LockSettings(math_lock_digits=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("passphrase", ["pw", None])
def test_splash_roundtrip(
    tmp_path: Path,
    passphrase: str | None,
) -> None:
    path = tmp_path / "s.db"
    original = replace(
        sidecar.create(passphrase),
        database_title="Personal secrets",
        database_description="Use for the home server",
    )
    sidecar.save(path, original)
    loaded = sidecar.load(path)
    assert loaded.database_title == "Personal secrets"
    assert loaded.database_description == "Use for the home server"


def test_load_legacy_sidecar_without_splash(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    payload = sidecar.create("pw").to_json()
    del payload["database_title"]
    del payload["database_description"]
    sidecar.sidecar_path(path).write_text(json.dumps(payload))
    loaded = sidecar.load(path)
    assert loaded.database_title == ""
    assert loaded.database_description == ""


@pytest.mark.parametrize("value", [1, None, ["x"]])
def test_load_rejects_invalid_splash(tmp_path: Path, value: object) -> None:
    path = tmp_path / "s.db"
    payload = sidecar.create("pw").to_json()
    payload["database_title"] = value
    sidecar.sidecar_path(path).write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        sidecar.load(path)


def test_derive_key_matches_crypto(tmp_path: Path) -> None:
    record = sidecar.create("pw")
    assert record.derive_key("pw") == derive_key("pw", record.salt, record.kdf)


def test_derive_key_plaintext_raises() -> None:
    with pytest.raises(ValueError):
        sidecar.create(None).derive_key("pw")


def test_save_requires_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        sidecar.save(tmp_path / "missing" / "s.db", sidecar.create(None))


def test_load_rejects_foreign_json(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    sidecar.sidecar_path(path).write_text(json.dumps({"format": "other"}))
    with pytest.raises(ValueError):
        sidecar.load(path)


def test_load_rejects_unknown_version(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    sidecar.sidecar_path(path).write_text(
        json.dumps({"format": "confirm-env", "version": 99}),
    )
    with pytest.raises(ValueError):
        sidecar.load(path)


def test_load_rejects_corrupt_kdf(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    payload = sidecar.create("pw").to_json()
    payload["kdf"]["time_cost"] = 0
    sidecar.sidecar_path(path).write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        sidecar.load(path)


def test_save_is_atomic_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    sidecar.save(path, sidecar.create("pw"))
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
