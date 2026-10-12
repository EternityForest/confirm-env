"""Tests for the sidecar KDF file."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from confirm_env import sidecar
from confirm_env.crypto import derive_key
from confirm_env.sidecar import EncryptionMode, Settings


def test_create_encrypted() -> None:
    record = sidecar.create("pw")
    assert record.encrypted
    assert record.encryption is EncryptionMode.SQLCIPHER
    assert record.salt is not None and record.kdf is not None


@pytest.mark.parametrize("empty", [None, ""])
def test_create_plaintext(empty: str | None) -> None:
    record = sidecar.create(empty)
    assert not record.encrypted
    assert record.encryption is EncryptionMode.NONE
    assert record.salt is None and record.kdf is None


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
    assert loaded.settings == original.settings


def test_settings_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "s.db"
    original = replace(
        sidecar.create(None),
        settings=Settings(unlock_delay=2.5),
    )
    sidecar.save(path, original)
    assert sidecar.load(path).settings.unlock_delay == 2.5


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
