"""Tests for the run-log helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from confirm_env import log


def test_capture_records_quoted_command_line() -> None:
    entry = log.capture(["bash", "-c", "echo hello world"])
    assert entry.command == "bash -c 'echo hello world'"


def test_capture_records_environment() -> None:
    entry = log.capture(["true"])
    assert entry.username
    assert entry.hostname
    assert entry.workdir == str(Path.cwd())


def test_capture_timestamp_is_local_iso() -> None:
    entry = log.capture(["true"])
    parsed = datetime.fromisoformat(entry.timestamp)
    assert parsed.tzinfo is not None


def test_capture_uses_given_workdir(tmp_path: Path) -> None:
    entry = log.capture(["true"], workdir=tmp_path)
    assert entry.workdir == str(tmp_path)


def test_username_falls_back_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> str:
        raise KeyError("no user")

    monkeypatch.setattr(log.getpass, "getuser", boom)
    assert log._username() == "unknown"


def test_hostname_falls_back_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom() -> str:
        raise OSError("no host")

    monkeypatch.setattr(log.socket, "gethostname", boom)
    assert log._hostname() == "unknown"
