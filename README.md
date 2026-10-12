# confirm-env

Run commands with environment variables that live in an encrypted database.

`confirm-env` can be used with [direnv](https://direnv.net),
[atuin](https://atuin.sh) or your own scripts, to protect your scripts from
mistakes.

It stores variables in a SQLCipher database, derives the key from a passphrase with argon2id, and
shows a short, cancellable delay before unlocking so an accidental
invocation is noticeable.

AI Assistance has been used in building this tool, and it has NOT
been audited by any security professionals.

## Use Cases

* Any time you want a bit more substantial confirmation prompt
* Use with a known public password as a simple extra friction step.
* Keep environment config on a flash drive and easily import it.


## Quick start

```console
# 1. Create a store and edit it in the GUI.
$ confirm-env -e secrets.db

# 2. Run a command with those variables in the environment.
# Note that just plain ` confirm-env secrets.db echo $test` 
# won't work, because  the var would get evaluated before confirm-env makes it available.
$ confirm-env secrets.db bash -c 'echo $test'

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


## The unlock delay

Unlocking announces the store and the command, then shows a progress bar
for a configurable delay (default **5 seconds**). Press `q`, `Esc` or
`Ctrl-C` to cancel. The delay lives in the sidecar and can be changed on the
**Metadata & Settings** page of the editor.

## Editing

`confirm-env -e FILE` opens a [Textual](https://textual.textualize.io) GUI
with two pages:

* **Variables** — add, edit and delete environment variables. Deleting asks
  for confirmation.
* **Metadata & Settings** — free-form metadata plus the unlock delay.

Changes are written with `Ctrl-S`; quitting with unsaved changes asks for
confirmation.

## License

MIT. See [LICENSE](LICENSE).
