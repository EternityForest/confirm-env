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

## Math lock

A store can require a small addition challenge before it will unlock, to
catch an absent-minded invocation. Set the number of digits (the width of
each addend) on the **Metadata & Settings** page; `0` disables the lock. The
challenge shares the passphrase's three-attempt budget, so a wrong answer
counts against the same limit.

## Database splash

A store can carry a title and a description, shown just before the
passphrase prompt so you can see exactly which store you are about to
open. Both live in the sidecar and can be set on the **Metadata &
Settings** page of the editor; leaving them empty hides the splash.

## Editing

`confirm-env -e FILE` opens a [Textual](https://textual.textualize.io) GUI
with two pages:

* **Variables** — add, edit and delete environment variables. Deleting asks
  for confirmation.
* **Metadata & Settings** — free-form metadata, the unlock splash title and
  description, plus the unlock delay and math lock.

Changes are written with `Ctrl-S`; quitting with unsaved changes asks for
confirmation.

## License

MIT. See [LICENSE](LICENSE).
