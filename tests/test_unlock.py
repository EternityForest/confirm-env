"""Tests for the interactive unlocking helpers."""

from __future__ import annotations

import io
import types
from collections.abc import Iterator

import pytest
from rich.console import Console

from confirm_env import unlock


def _console() -> Console:
    return Console(file=io.StringIO(), width=80)


def _reader(values: list[str]) -> object:
    iterator: Iterator[str] = iter(values)
    return lambda prompt="": next(iterator)


def test_confirm_delay_zero_returns_true() -> None:
    assert unlock.confirm_delay(0) is True


def test_confirm_delay_negative_raises() -> None:
    with pytest.raises(ValueError):
        unlock.confirm_delay(-1)


def test_confirm_delay_cancel_is_immediate() -> None:
    cancelled = unlock.confirm_delay(
        5,
        console=_console(),
        cancel_check=lambda: True,
    )
    assert cancelled is False


def test_confirm_delay_completes() -> None:
    assert unlock.confirm_delay(0.05, console=_console()) is True


def test_key_listener_is_noop_without_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_stdin = types.SimpleNamespace(isatty=lambda: False, fileno=lambda: 0)
    monkeypatch.setattr(unlock.sys, "stdin", fake_stdin)
    with unlock.KeyListener() as listener:
        assert listener.cancelled() is False


def test_prompt_new_passphrase_match() -> None:
    result = unlock.prompt_new_passphrase(
        console=_console(),
        read=_reader(["pw", "pw"]),
    )
    assert result == "pw"


def test_prompt_new_passphrase_mismatch_then_match() -> None:
    result = unlock.prompt_new_passphrase(
        console=_console(),
        read=_reader(["a", "b", "c", "c"]),
    )
    assert result == "c"


def test_prompt_new_passphrase_empty_confirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda *args: "y")
    result = unlock.prompt_new_passphrase(
        console=_console(),
        read=_reader([""]),
    )
    assert result is None


def test_prompt_new_passphrase_empty_rejected_then_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda *args: "n")
    result = unlock.prompt_new_passphrase(
        console=_console(),
        read=_reader(["", "pw", "pw"]),
    )
    assert result == "pw"


def test_confirm_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *args: "")
    assert unlock.confirm("ok?") is False
    assert unlock.confirm("ok?", default=True) is True


def test_confirm_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda *args: "yes")
    assert unlock.confirm("ok?") is True


def test_math_addends_have_requested_width() -> None:
    for _ in range(50):
        left, right = unlock._math_addends(3)
        assert 100 <= left <= 999
        assert 100 <= right <= 999


def test_math_addends_reject_zero_digits() -> None:
    with pytest.raises(ValueError):
        unlock._math_addends(0)


def test_prompt_math_lock_correct() -> None:
    def read(question: str) -> str:
        left, right = (int(part) for part in question.rstrip(" =").split(" + "))
        return str(left + right)

    assert unlock.prompt_math_lock(2, read=read) is True


def test_prompt_math_lock_wrong() -> None:
    assert unlock.prompt_math_lock(2, read=lambda question: "0") is False


def test_prompt_math_lock_non_numeric() -> None:
    assert unlock.prompt_math_lock(2, read=lambda question: "abc") is False
