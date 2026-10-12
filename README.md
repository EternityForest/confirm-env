# confirm-env

Run commands with environment variables that live in an encrypted database,
and keep that database on a USB stick without worrying about mistakes.

`confirm-env` sits in front of tools like [direnv](https://direnv.net),
[atuin](https://atuin.sh) or your own scripts. It stores variables in a
SQLCipher database, derives the key from a passphrase with argon2id, and
shows a short, cancellable delay before unlocking so an accidental
invocation is noticeable.

## Quick start

```console
# 1. Create a store and edit it in the GUI.
$ confirm-env -e secrets.db

# 2. Run a command with those variables in the environment.
$ confirm-env secrets.db -- my-web-app --serve

# 3. Or print them for a shell to source (direnv friendly).
$ confirm-env -p secrets.db
export API_KEY='...'
export DATABASE_URL='...'
```

The store is a pair of files that travel together:

| File                  | Contents                                        |
| --------------------- | ----------------------------------------------- |
| `secrets.db`          | the SQLCipher database (variables + metadata)   |
| `secrets.db.sidecar.json` | the argon2id salt and parameters            |

## Why a sidecar?

SQLCipher's built-in key derivation is an old, low-iteration PBKDF2.
`confirm-env` instead derives a 256-bit key itself with **argon2id** and
hands SQLCipher that raw key, so SQLCipher never runs its legacy KDF. The
salt and parameters are not secret and are kept in the sidecar file so they
remain readable before the database is opened.

## Security properties

* The passphrase is **never stored**; only its salt and KDF parameters are.
* A wrong passphrase is rejected by SQLCipher (HMAC failure), not by a
  separate password check, so there is nothing extra to leak.
* The database is unreadable as plain SQLite.
* An **empty passphrase disables SQLCipher** and produces a plain, portable
  SQLite file. You are asked to confirm before this happens.

## The unlock delay

Unlocking announces the store and the command, then shows a progress bar
for a configurable delay (default **5 seconds**). Press `q`, `Esc` or
`Ctrl-C` to cancel. The delay lives in the sidecar and can be changed on the
**Metadata & Settings** page of the editor, or overridden per run with
`--delay SECONDS`.

## Editing

`confirm-env -e FILE` opens a [Textual](https://textual.textualize.io) GUI
with two pages:

* **Variables** — add, edit and delete environment variables. Deleting asks
  for confirmation.
* **Metadata & Settings** — free-form metadata plus the unlock delay.

Changes are written with `Ctrl-S`; quitting with unsaved changes asks for
confirmation.

## Dependencies

* **[sqlcipher3-binary](https://pypi.org/project/sqlcipher3-binary/)** —
  SQLCipher bindings that ship a bundled SQLCipher, so there is no system
  library to install. It exposes the same `sqlcipher3.dbapi2` API as
  `pysqlcipher3`, so the database module stays portable if you prefer to
  build against a system SQLCipher.
* **[argon2-cffi](https://pypi.org/project/argon2-cffi/)** for key
  derivation.
* **[Textual](https://textual.textualize.io)** for the editor GUI.

## Development

`confirm-env` uses [uv](https://docs.astral.sh/uv) and a `Makefile`:

```console
$ make sync    # create the virtual environment
$ make check   # lint and test
$ make format  # auto-format
```

## License

MIT. See [LICENSE](LICENSE).
