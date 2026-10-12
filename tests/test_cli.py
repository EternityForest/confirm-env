"""Tests for the command line interface."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from rich.console import Console

from confirm_env import cli, sidecar
from confirm_env.database import Database

PASSPHRASE = "pw"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "s.db"
    record = sidecar.create(PASSPHRASE)
    database = Database.create(path, record.derive_key(PASSPHRASE))
    database.set_env("GREETING", "hello world")
    database.close()
    sidecar.save(path, record)
    return path


@pytest.fixture(autouse=True)
def _fast_unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the delay and always supply the right passphrase."""
    monkeypatch.setattr(cli, "confirm_delay", lambda *a, **k: True)
    monkeypatch.setattr(cli, "prompt_passphrase", lambda *a, **k: PASSPHRASE)


def test_print_env(store: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["-p", str(store)]) == 0
    out = capsys.readouterr().out
    assert "export GREETING='hello world'" in out


def test_print_env_plaintext(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "p.db"
    record = sidecar.create(None)
    database = Database.create(path, None)
    database.set_env("A", "1")
    database.close()
    sidecar.save(path, record)

    assert cli.main(["-p", str(path)]) == 0
    assert "export A=1" in capsys.readouterr().out


def test_run_command_forwards_exit_code(store: Path) -> None:
    code = cli.main(
        [str(store), sys.executable, "-c", "import sys; sys.exit(3)"],
    )
    assert code == 3


def test_run_command_sets_environment(
    store: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    program = "import os; print(os.environ['GREETING'])"
    assert cli.main([str(store), sys.executable, "-c", program]) == 0
    assert "hello world" in capfd.readouterr().out


def test_run_command_not_found(
    store: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main([str(store), "definitely-not-a-real-command-xyz"]) == 1
    assert "not found" in capsys.readouterr().out


def test_missing_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["-p", str(tmp_path / "nope.db")]) == 1
    assert "no store" in capsys.readouterr().out


def test_missing_sidecar(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "orphan.db"
    path.write_bytes(b"not a real database")
    assert cli.main(["-p", str(path)]) == 1
    assert "missing sidecar" in capsys.readouterr().out


def test_wrong_passphrase(
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "prompt_passphrase", lambda *a, **k: "wrong")
    assert cli.main(["-p", str(store)]) == 1
    assert "Wrong passphrase" in capsys.readouterr().out


def test_print_rejects_command(store: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["-p", str(store), "true"])


def test_requires_command(store: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main([str(store)])


def test_export_lines_quotes_values() -> None:
    lines = cli.export_lines({"B": "two", "A": "one two"})
    assert lines == ["export A='one two'", "export B=two"]


def _console() -> Console:
    return Console(file=io.StringIO(), width=80)


def test_create_store_encrypted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "prompt_new_passphrase", lambda **k: "secret")
    path = tmp_path / "new.db"
    database, record = cli.create_store(path, _console())
    database.set_env("A", "1")
    database.close()

    assert record.encrypted
    assert sidecar.exists(path)
    reopened = Database.open(path, record.derive_key("secret"))
    try:
        assert reopened.get_env("A") == "1"
    finally:
        reopened.close()


def test_create_store_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "prompt_new_passphrase", lambda **k: None)
    path = tmp_path / "new.db"
    database, record = cli.create_store(path, _console())
    database.close()
    assert not record.encrypted


def test_create_store_cleans_up_on_sidecar_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "prompt_new_passphrase", lambda **k: "secret")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cli.sidecar, "save", boom)
    path = tmp_path / "new.db"
    with pytest.raises(OSError):
        cli.create_store(path, _console())
    assert not path.exists()


def test_run_editor_rejects_orphan_database(tmp_path: Path) -> None:
    path = tmp_path / "orphan.db"
    path.write_bytes(b"not a database")
    with pytest.raises(cli.CliError):
        cli.run_editor(path, None, _console())


def test_delay_parsing_rejects_negative() -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        cli._delay_seconds("-1")
    with pytest.raises(argparse.ArgumentTypeError):
        cli._delay_seconds("abc")
    assert cli._delay_seconds("2.5") == 2.5
