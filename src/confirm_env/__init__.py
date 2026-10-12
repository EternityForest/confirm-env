"""confirm-env: run commands with environment variables held in an encrypted
SQLCipher database.

The package is split into small, single-purpose modules:

``crypto``    argon2id key derivation for the database key.
``sidecar``   the JSON file that records the KDF salt and parameters.
``database``  the SQLCipher-backed store for variables, metadata, settings.
``unlock``    interactive unlocking: target banner, delay, passphrase.
``editor``    the Textual GUI for editing a store.
``cli``       argument parsing and command dispatch.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
