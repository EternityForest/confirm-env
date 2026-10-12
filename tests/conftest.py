"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from confirm_env import sidecar
from confirm_env.database import Database

PASSPHRASE = "correct horse battery staple"


@pytest.fixture
def passphrase() -> str:
    return PASSPHRASE


@pytest.fixture
def encrypted_store(tmp_path: Path, passphrase: str) -> Path:
    """An encrypted store containing one variable and some metadata."""
    path = tmp_path / "secrets.db"
    record = sidecar.create(passphrase)
    database = Database.create(path, record.derive_key(passphrase))
    database.set_env("FOO", "bar")
    database.set_metadata("description", "test store")
    database.close()
    sidecar.save(path, record)
    return path


@pytest.fixture
def plain_store(tmp_path: Path) -> Path:
    """An unencrypted store containing one variable."""
    path = tmp_path / "plain.db"
    record = sidecar.create(None)
    database = Database.create(path, None)
    database.set_env("FOO", "bar")
    database.close()
    sidecar.save(path, record)
    return path
