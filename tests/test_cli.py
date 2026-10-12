"""Tests for the command line interface."""

from __future__ import annotations

import io
import shlex
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from rich.console import Console

from confirm_env import cli, sidecar
from confirm_env.database import Database
from confirm_env.sidecar import LockSettings

PASSPHRASE = "pw"


def _open_store(path: Path) -> Database:
    """Open a store written by the CLI, deriving its key from the sidecar."""
    record = sidecar.load(path)
    key = record.derive_key(PASSPHRASE) if record.encrypted else None
    return Database.open(path, key, record.cipher_compatibility)


@pytest.fixture
def store(tmp_path: Path) -> Path:
    path = tmp_path / "s.db"
    record = sidecar.create(PASSPHRASE)
    database = Database.create(path, record.derive_key(PASSPHRASE))
    database.set_env("GREETING", "hello world")
    database.close()
    sidecar.save(path, record)
    return path


@pytest.fixture
def locked_store(tmp_path: Path) -> Path:
    """An encrypted store that also requires a two-digit math lock."""
    path = tmp_path / "locked.db"
    record = sidecar.create(PASSPHRASE)
    record = replace(record, lock_settings=LockSettings(math_lock_digits=2))
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
    assert cli.main(["--print", str(store)]) == 0
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

    assert cli.main(["--print", str(path)]) == 0
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


def test_run_command_logs_entry(store: Path) -> None:
    program = "pass"
    command = [sys.executable, "-c", program]
    assert cli.main([str(store), *command]) == 0

    database = _open_store(store)
    try:
        entries = database.log_page(0, 10)
    finally:
        database.close()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.command == shlex.join(command)
    assert entry.username
    assert entry.hostname
    assert entry.workdir == str(Path.cwd())


def test_print_env_does_not_log(store: Path) -> None:
    assert cli.main(["--print", str(store)]) == 0
    database = _open_store(store)
    try:
        assert database.count_log() == 0
    finally:
        database.close()


def test_missing_store(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["--print", str(tmp_path / "nope.db")]) == 1
    assert "no store" in capsys.readouterr().out


def test_missing_sidecar(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "orphan.db"
    path.write_bytes(b"not a real database")
    assert cli.main(["--print", str(path)]) == 1
    assert "missing sidecar" in capsys.readouterr().out


def test_wrong_passphrase(
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "prompt_passphrase", lambda *a, **k: "wrong")
    assert cli.main(["--print", str(store)]) == 1
    assert "Wrong passphrase" in capsys.readouterr().out


def test_math_lock_correct_answer_unlocks(
    locked_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "prompt_math_lock", lambda *a, **k: True)
    assert cli.main(["--print", str(locked_store)]) == 0
    assert "export GREETING='hello world'" in capsys.readouterr().out


def test_math_lock_wrong_answers_exhaust_attempts(
    locked_store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "prompt_math_lock", lambda *a, **k: False)
    assert cli.main(["--print", str(locked_store)]) == 1
    out = capsys.readouterr().out
    assert "Wrong answer" in out
    assert "too many failed unlock attempts" in out


def test_math_lock_shares_attempt_budget(
    locked_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One wrong answer plus two wrong passphrases exhausts the budget, so
    # the passphrase is only ever tried twice.
    answers = iter([False, True, True])
    monkeypatch.setattr(
        cli,
        "prompt_math_lock",
        lambda *a, **k: next(answers),
    )
    passphrase_calls = 0

    def counting_passphrase(*args: object, **kwargs: object) -> str:
        nonlocal passphrase_calls
        passphrase_calls += 1
        return "wrong"

    monkeypatch.setattr(cli, "prompt_passphrase", counting_passphrase)
    assert cli.main(["--print", str(locked_store)]) == 1
    assert passphrase_calls == 2


def test_print_rejects_command(store: Path) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--print", str(store), "true"])


def test_unlock_shows_splash(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "splash.db"
    record = replace(
        sidecar.create(PASSPHRASE),
        database_title="Personal secrets",
        database_description="Home server",
    )
    database = Database.create(path, record.derive_key(PASSPHRASE))
    database.set_env("A", "1")
    database.close()
    sidecar.save(path, record)

    assert cli.main(["--print", str(path)]) == 0
    out = capsys.readouterr().out
    assert "Personal secrets" in out
    assert "Home server" in out


def test_plaintext_store_shows_splash(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "splash.db"
    record = replace(sidecar.create(None), database_title="Plain store")
    database = Database.create(path, None)
    database.set_env("A", "1")
    database.close()
    sidecar.save(path, record)

    assert cli.main(["--print", str(path)]) == 0
    assert "Plain store" in capsys.readouterr().out


def test_unlock_recovers_interrupted_migration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "s.db"
    record = sidecar.create(PASSPHRASE)
    key = record.derive_key(PASSPHRASE)
    database = Database.create(store, key)
    database.set_env("GREETING", "hi")
    database.close()
    sidecar.save(store, record)

    # Reproduce a crash mid re-encryption: the new database was swapped in,
    # the new sidecar is still in its temporary file and the original
    # database survives as the backup.
    new_record = sidecar.create("other")
    new_key = new_record.derive_key("other")
    source = Database.open(store, key)
    try:
        source.export_to(tmp_path / "s.db.reencrypt", new_key)
    finally:
        source.close()
    sidecar.write(tmp_path / "s.db.sidecar.toml.reencrypt", new_record)
    shutil.copy(store, tmp_path / "s.db.previous")
    (tmp_path / "s.db.reencrypt").replace(store)

    # Unlocking first rolls the store back to the original passphrase.
    assert cli.main(["--print", str(store)]) == 0
    assert "export GREETING=hi" in capsys.readouterr().out
    assert not (tmp_path / "s.db.previous").exists()
    assert not (tmp_path / "s.db.sidecar.toml.reencrypt").exists()


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


def test_edit_mode_ignores_title_and_description(
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_splash(title: str, description: str, **kwargs: object) -> bool:
        calls.append((title, description))
        return True

    monkeypatch.setattr(cli, "announce_command_splash", fake_splash)
    monkeypatch.setattr(cli, "run_editor", lambda *a, **k: 0)

    code = cli.main(
        [
            "-e",
            str(store),
            "--title",
            "Danger",
            "--description",
            "Will wipe data",
        ],
    )

    assert code == 0
    assert calls == []


def test_delay_parsing_rejects_negative() -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError):
        cli._delay_seconds("-1")
    with pytest.raises(argparse.ArgumentTypeError):
        cli._delay_seconds("abc")
    assert cli._delay_seconds("2.5") == 2.5


class _InteractiveStdin:
    """A stand-in for a terminal stdin in picker tests."""

    def isatty(self) -> bool:
        return True


def _point_providers_at(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    config = tmp_path / "providers.toml"
    monkeypatch.setattr(cli.providers, "providers_path", lambda: config)
    monkeypatch.delenv(cli.providers.ENV_VAR, raising=False)
    return config


def test_set_provider_records_mapping(
    store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _point_providers_at(monkeypatch, tmp_path)

    assert cli.main(["--set-provider", "work", str(store)]) == 0
    assert cli.providers.lookup("work") == store
    assert "work" in capsys.readouterr().out


def test_set_provider_rejects_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _point_providers_at(monkeypatch, tmp_path)

    code = cli.main(
        ["--set-provider", "work", str(tmp_path / "missing.db")],
    )
    assert code == 1
    assert "no such file" in capsys.readouterr().out


def test_provider_resolves_and_runs_command(
    store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _point_providers_at(monkeypatch, tmp_path)
    cli.providers.set_provider("work", store)

    code = cli.main(
        [
            "--provider",
            "work",
            sys.executable,
            "-c",
            "import os; print(os.environ['GREETING'])",
        ],
    )
    assert code == 0
    assert "hello world" in capfd.readouterr().out


def test_provider_uses_env_override(
    store: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(cli.providers.ENV_VAR, f"work:{store}")

    code = cli.main(
        ["--provider", "work", sys.executable, "-c", "print('ok')"],
    )
    assert code == 0
    assert "ok" in capfd.readouterr().out


def test_provider_missing_noninteractive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _point_providers_at(monkeypatch, tmp_path)

    code = cli.main(["--provider", "nope", sys.executable, "-c", "pass"])
    assert code == 1
    assert "no provider named" in capsys.readouterr().out


def test_provider_points_at_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _point_providers_at(monkeypatch, tmp_path)
    cli.providers.save({"work": str(tmp_path / "gone.db")})

    code = cli.main(["--provider", "work", sys.executable, "-c", "pass"])
    assert code == 1
    assert "missing file" in capsys.readouterr().out


def test_provider_picker_persists_choice(
    store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _point_providers_at(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.sys, "stdin", _InteractiveStdin())
    monkeypatch.setattr(cli.picker, "pick_file", lambda *a, **k: store)

    code = cli.main(
        ["--provider", "work", sys.executable, "-c", "print('picked')"],
    )
    assert code == 0
    assert "picked" in capfd.readouterr().out
    assert cli.providers.lookup("work") == store


def test_provider_picker_cancel_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _point_providers_at(monkeypatch, tmp_path)
    monkeypatch.setattr(cli.sys, "stdin", _InteractiveStdin())
    monkeypatch.setattr(cli.picker, "pick_file", lambda *a, **k: None)

    code = cli.main(["--provider", "work", sys.executable, "-c", "pass"])
    assert code == 130


def test_missing_file_and_provider_is_error() -> None:
    with pytest.raises(SystemExit):
        cli.main([])
