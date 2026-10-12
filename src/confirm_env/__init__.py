"""confirm-env: run commands with environment variables held in an encrypted
SQLCipher database.

The package is split into small, single-purpose modules:

``crypto``      argon2id key derivation for the database key.
``atomicwrite`` atomic, durable file writes shared by the config files.
``sidecar``     the TOML file that records the KDF salt and parameters.
``database``    the SQLCipher-backed store for variables, metadata, settings.
``log``         the bounded audit trail of commands that have been run.
``migrate``     re-encryption to a new passphrase and current cipher format.
``unlock``      interactive unlocking: target banner, delay, passphrase.
``editor``      the Textual GUI for editing a store.
``picker``      the Textual file picker used to choose provider stores.
``providers``   saved store locations, keyed by provider name.
``cli``         argument parsing and command dispatch.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
