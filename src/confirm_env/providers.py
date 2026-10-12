"""User-level provider mappings for confirm-env store locations.

A *provider* maps a short name to a store path so scripts can refer to a
store without hard-coding where it lives. The mapping lives in the user's
config directory (``providers.toml``) and can be overridden for a single
command with the ``CONFIRM_ENV_PROVIDERS`` environment variable.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import platformdirs
import tomli_w

from confirm_env.atomicwrite import atomic_write_text

#: Application name used to locate the config directory.
APP_NAME = "confirm-env"

#: Environment variable that bypasses the providers file entirely.
ENV_VAR = "CONFIRM_ENV_PROVIDERS"

#: Name of the providers file inside the user config directory.
FILENAME = "providers.toml"


def config_dir() -> Path:
    """Return the user config directory for confirm-env."""
    return Path(platformdirs.user_config_dir(APP_NAME))


def providers_path() -> Path:
    """Return the path of the providers file."""
    return config_dir() / FILENAME


def parse_env(value: str) -> dict[str, str]:
    """Parse a ``name:file,name:file`` provider override string.

    Each entry is split on its *first* colon so Windows drive letters such
    as ``C:\\store.db`` are preserved.
    """
    mapping: dict[str, str] = {}
    for item in value.split(","):
        entry = item.strip()
        if not entry:
            continue
        name, sep, target = entry.partition(":")
        name = name.strip()
        target = target.strip()
        if not sep or not name or not target:
            raise ValueError(f"invalid provider override: {entry!r}")
        mapping[name] = target
    return mapping


def load(path: Path | None = None) -> dict[str, str]:
    """Read the providers file, returning an empty mapping when absent."""
    source = path or providers_path()
    if not source.is_file():
        return {}
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    return _validate(data)


def save(mapping: dict[str, str], path: Path | None = None) -> None:
    """Atomically write the providers ``mapping``."""
    target = path or providers_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(target, tomli_w.dumps(mapping))


def lookup(name: str) -> Path | None:
    """Resolve ``name`` to a store path, or ``None`` when unknown.

    ``CONFIRM_ENV_PROVIDERS`` bypasses the providers file entirely: when
    it is set, only the names it lists can be resolved.
    """
    override = os.environ.get(ENV_VAR)
    raw = (
        parse_env(override).get(name)
        if override is not None
        else load().get(name)
    )
    return Path(raw) if raw else None


def set_provider(name: str, path: Path) -> None:
    """Record ``name`` as pointing at ``path``.

    The path is expanded and must name an existing file, so a provider
    cannot be saved pointing at a typo.
    """
    key = name.strip()
    if not key:
        raise ValueError("provider name must not be empty")
    resolved = path.expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"no such file: {resolved}")
    mapping = load()
    mapping[key] = str(resolved)
    save(mapping)


def _validate(data: Any) -> dict[str, str]:
    """Check that ``data`` is a flat table of name -> path strings."""
    if not isinstance(data, dict):
        raise ValueError("providers file must contain a table")
    mapping: dict[str, str] = {}
    for name, value in data.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ValueError("providers file must map names to paths")
        mapping[name] = value
    return mapping
