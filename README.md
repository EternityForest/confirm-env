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
$ confirm-env --print secrets.db
export API_KEY='...'
export DATABASE_URL='...'
```

The store is a pair of files that travel together:

| File                      | Contents                                     |
| ------------------------- | -------------------------------------------- |
| `secrets.db`              | the SQLCipher database (variables + metadata) |
| `secrets.db.sidecar.toml` | the argon2id salt and parameters              |

Stores created by older versions used a `.sidecar.json` file. `confirm-env`
still reads that format, and removes it the next time the store is saved as
TOML, so a store never keeps two sidecars.

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
each addend) on the **Metadata & Settings** page; `0` disables the lock.


## Pre-run delay

With the `--delay 5` syntax, you can add an extra delay after all user interation
is done, to give the user time to realize the terrible mistake they made and cancel it.

## Database splash

A store can carry a title and a description, shown just before the
passphrase prompt so you can see exactly which store you are about to
open. 

Both live in the sidecar and can be set on the **Metadata &
Settings** page of the editor; leaving them empty hides the splash.

## Command splash

Pass `--title` (and optionally `--description`) when running a command to
show a confirmation box *before* the unlock delay starts:

```console
$ confirm-env --title "Deploy" --description "Pushing to production" \
    secrets.db deploy.sh
```

The box waits for any key, so you can read exactly what you are about to
do. Both options are ignored in edit mode.

## Providers

Providers let you keep generic scripts instead of hard-coding the path to a
store. The mappings live in a single config file discovered with
[platformdirs](https://pypi.org/project/platformdirs/)
(`providers.toml` in your user config directory).

```console
# Record a store under a name (the file must already exist).
$ confirm-env --set-provider work secrets.db

# Resolve it by name when running a command.
$ confirm-env --provider work bash -c 'echo $API_KEY'
```

If a name is not known, an interactive `confirm-env` shows a Textual file
picker and remembers the choice; in a non-interactive session it fails with
a clear error.

`confirm-env --edit-providers` opens a GUI listing every provider in the
same key/value table used for a store's variables. Adding or editing one
picks the store with the same file browser, so a provider can never be saved
pointing at a path that does not exist.

```console
$ confirm-env --edit-providers
```

You can bypass the lookup entirely with the `CONFIRM_ENV_PROVIDERS`
environment variable, which takes a comma-separated list of `name:file`
pairs:

```console
$ CONFIRM_ENV_PROVIDERS="work:/mnt/usb/secrets.db" \
    confirm-env --provider work bash -c 'echo $API_KEY'
```

## Editing

`confirm-env -e FILE` opens a [Textual](https://textual.textualize.io) GUI
with three pages:

* **Variables** — add, edit and delete environment variables. Deleting asks
  for confirmation.
* **Metadata & Settings** — free-form metadata, the unlock splash title and
  description, plus the unlock delay and math lock.
* **Log** — a read-only, paginated view of the store's run log.

Changes are written with `Ctrl-S`; quitting with unsaved changes asks for
confirmation.

## Run log

Every command run through `confirm-env` is recorded inside the encrypted
database: the command line, the local time, the username, the working
directory and the hostname. The **Log** page of the editor shows these
newest-first, one page at a time.

The log is bounded so the store cannot grow without limit: once it holds
more than 1000 entries, the 100 oldest are dropped. `--print` and editing a
store do not add entries.

## Real TTY Requirements

To avoid temptation to bypass it, `confirm-env` will not allow any action that would normally require the unlock process unless the stdin is a TTY, blocking piped input scripts.

You can get around this with more involved simulated terminals, but future updates will likely have code to detect some of those too.


## License

MIT. See [LICENSE](LICENSE).
