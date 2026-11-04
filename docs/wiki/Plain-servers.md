# Without Django

The passes do not need a Django project. A server that runs a database and a service — a
self-hosted application, an identity provider, a registry — gets the same backups from the
`offsite-backup` command: the same stores and Object Lock read back, the same `age`
encryption, the same sources, whole-night rotation, morning check, drill and messages. The
setting is a TOML file whose top level is what `OFFSITE_BACKUP` holds in a Django project.

It is also what replaces a server's own copy of a backup script: the script keeps deciding
**what** leaves (it knows the service), and hands the files to `offsite-backup`, which
decides **how** — and stops drifting from every other copy.

## Install

The command needs Python 3.10+ and no Django. Install it as a tool, with the extras the
setting names:

```shell
pipx install 'django-offsite-backup[cli,s3,telegram]'
# or: uv tool install 'django-offsite-backup[cli,s3,telegram]'
offsite-backup --version
```

`[cli]` adds `tomli` on Python 3.10 only (3.11+ read TOML with the standard library). The
server needs the same binaries as any pass: `zstd`, `age`, and `pg_dump` / `psql` /
`pg_restore` of at least the database server's major for a PostgreSQL source
([Installation](Installation.md#binaries)). `python -m offsite_backup` is the same command.

## The setting

```toml
# /etc/offsite-backup/registry.toml — root, 0600
PREFIX = "acme/registry"
ENV = "prod"
NAME = "the registry"

[STORE]
BACKEND = "offsite_backup.stores.s3.S3Store"
ENDPOINT_URL = "https://fsn1.your-objectstorage.com"
REGION = "fsn1"
BUCKET = "acme-backups"
ACCESS_KEY = "${BACKUP_S3_ACCESS_KEY}"
SECRET_KEY = "${BACKUP_S3_SECRET_KEY}"
LOCK_DAYS = 13

[ENCRYPTION]
BACKEND = "offsite_backup.encryption.age_cli.AgeCli"
RECIPIENTS = "age1..."

[DATABASES]
BACKEND = "offsite_backup.databases.postgres.PostgresSource"
FORMAT = "custom"
ALIASES = ["default"]

[DATABASES.CONNECTIONS.default]
HOST = "127.0.0.1"
PORT = "5432"
USER = "registry"
PASSWORD = "${REGISTRY_DB_PASSWORD}"
NAME = "registry"

[CONFIG]
BACKEND = "offsite_backup.config.paths.PathsSource"
PATHS = ["/srv/registry/docker-compose.yml", "/srv/registry/config"]

[RETENTION]
KEEP_NIGHTS = 14

[[NOTIFY]]
BACKEND = "offsite_backup.notifiers.telegram.TelegramNotifier"
TOKEN = "${BACKUP_TELEGRAM_TOKEN}"
CHAT_ID = "${BACKUP_TELEGRAM_CHAT_ID}"
TOPIC_ID = "${BACKUP_TELEGRAM_TOPIC_ID}"
```

Every key and option is the one [Settings](Settings.md) describes, with its default and its
check. What differs off Django:

* **Variables.** A string may name environment variables: `${NAME}`, or `${NAME:-default}`;
  `$$` is a literal `$`. An unset variable is blank — and blank is unset, so a missing
  secret is still named by `E001` instead of read as a value. Only strings expand; write
  numbers (`LOCK_DAYS`, `KEEP_NIGHTS`) as TOML numbers.
* **`--env-file FILE`** (repeatable) reads `KEY=value` lines — `#` comments, an optional
  `export`, one pair of quotes dropped — for the variables. Later files win over earlier
  ones and over the process environment. Keep the secrets there, root-only, and the TOML
  file free of them: neither ever reaches argv.
* **Databases** are named in `CONNECTIONS`, an alias each, with the keys Django's
  `DATABASES` has (`HOST`, `PORT`, `USER`, `PASSWORD`, `NAME`, `OPTIONS`). `ALIASES` says
  which are dumped — `['default']` unless set ([Databases](Databases.md#the-connection)).
  `MySQLSource` and `SQLiteSource` take `CONNECTIONS` the same way (for SQLite, `NAME` is
  the file).
* **Every backend runs here but one.** The stores (one or [several](Stores-several.md)),
  both sealers (`age` and `Pyrage`), every database source, and the media sources
  `DirectorySource`, `S3Source` and `VolumeSource` (a Docker volume, through the `docker`
  CLI) need no Django. `StorageSource` reads a Django storage and is refused by name:
  `E002`, with `[django]` as the extra to install where Django is absent, or "needs a Django
  project" where it is installed and there is no project.
* **Paths** are best written absolute; a relative one is the working directory's, as under
  Django. `RENDER_ENV` is the exception: it is read against the setting file's directory,
  where Django would read `BASE_DIR`. A media `DirectorySource` needs its `ROOT` — there is
  no `MEDIA_ROOT` to default to.
* **No guard.** The once-per-night guard is a Django cache or a database lock; cron fires
  once, so it is not needed. Wrap the line in `flock -n` if a slow pass could meet the next.
* **The checks** are `offsite-backup checks` rather than `manage.py check` — the same
  codes, without the ones about Django's cache (`W003`) and test runner (`W004`).

`--config FILE` names the setting, or `OFFSITE_BACKUP_CONFIG` in the environment.

## Commands

| command | what | exit |
| --- | --- | --- |
| `run [--without-media] [--reason R] [--only KIND] [--path P]` | a pass now; `--path` adds a file or directory to the configuration archive (repeatable) — or, with no `[CONFIG]`, makes one of them alone | 0, 1 on failure |
| `check` | the morning check — MISSING / UNCHECKED said through the notifiers | 0, 1 |
| `status [--json] [--prometheus FILE] [--stale-hours H]` | the newest whole backup's age per kind, said to nobody ([Monitoring](Monitoring.md#the-age-of-every-kind)) | 0 fresh, 1 stale, 2 store unreadable |
| `list [--kind K] [--store NAME] [--json]` | what the store holds per kind | 0, 1 |
| `restore --stamp S [--identity F] [--into DIR] [--kind K] [--database ALIAS] [--path P] [--store NAME]` | download, decrypt, verify — one database (`--database` narrows `--kind`), or some media files (`--path`, repeatable, checked against the manifests); `--apply --database ALIAS [--from-database A] [--clean]` loads a `CONNECTIONS` alias ([Restore](Restore.md)) | 0, 1 |
| `drill [--identity F] [--database ALIAS] [--without-media]` | the restore drill ([Restore](Restore.md#the-monthly-drill)) | 0, 1 |
| `checks [--deploy]` | the system checks, printed; `--deploy` adds the bucket's lock, `pg_dump` against the server, the paths and the drill's target | 0, 1 on an error, **3 when backups are off** |
| `failed [--reason R] TEXT` | say that something *before* the pass failed — a dump that died in the caller's script — as the same FAILED message | 0 |

Every command but `checks`, `restore` and `drill` says "not configured on this machine,
nothing to do" and exits 0 where backups are off; a usage error (no setting, a file that is
not there, TOML that does not parse) is 2. A failure is printed on stderr in one line; the
`offsite_backup` logger writes to stderr as well ([Monitoring](Monitoring.md#the-log)).

## Scheduling

```cron
CRON_TZ=UTC
0 3 * * *  root  flock -n /run/offsite-backup.lock offsite-backup --config /etc/offsite-backup/registry.toml --env-file /etc/offsite-backup/registry.env run --reason nightly
0 9 * * *  root  offsite-backup --config /etc/offsite-backup/registry.toml --env-file /etc/offsite-backup/registry.env check
```

Or set `OFFSITE_BACKUP_CONFIG` in the crontab and drop `--config`. A systemd timer works the
same way; the exit status is what `OnFailure=` sees.

## A service that makes its own backup

Some services have their own backup tool, which writes an archive the database dump alone
does not replace. Keep the script that knows the service; let it hand the result over:

```sh
#!/bin/sh
set -eu
reason=${1:-nightly}
backup() { offsite-backup --config /etc/offsite-backup/app.toml --env-file /etc/offsite-backup/app.env "$@"; }

# off is not a failure; configured by halves is
state=0; backup checks >/dev/null || state=$?
[ "$state" -eq 3 ] && exit 0
[ "$state" -eq 0 ] || exit 1

work=$(mktemp -d /var/backups/app/.work-XXXXXX)
trap 'rm -rf "$work"' EXIT
app-backup-tool --out "$work/app.tar" \
  || { backup failed --reason "$reason" "the application backup failed; the journal has why"; exit 1; }

backup run --reason "$reason" --path "$work/app.tar" --path /srv/app/app.conf
```

* `checks` answers 3 for "off", 1 for "configured by halves", 0 for "on" — the three
  states, for a script.
* `failed` reports a failure the script met before the pass, in the same message format, so
  the chat reads the same whichever stage broke.
* `run --reason upgrade`, before an upgrade the script performs, says on failure that the
  upgrade stops and the running service (`NAME`) keeps serving.
* `--path` files go into the configuration archive, `<PREFIX>/config/<stamp>.tar.zst.age`,
  last in the pass — so the morning check, `status` and rotation read it like any other.
* `LAST_SUCCESS_FILE` writes the time of every successful pass, for a monitor that reads a
  file's age.

### Coming from a hand-written script

A script that wrote `<PREFIX>/<stamp>.tar.zst.age` directly under its prefix leaves those
objects where they are: the package writes under `<PREFIX>/<kind>/` and rotates only inside
each kind's folder, so it never reads or deletes the old layout. Delete the old objects by
hand once their locks have run out — or start the package under a new prefix. The message
format is the package's; the reasons `nightly`, `deploy`, `manual` and `upgrade` read as
before.

## From Python

The same entry point, for a deployment tool written in Python:

```python
from offsite_backup.cli import main

exit_code = main(['--config', '/etc/offsite-backup/app.toml', 'run', '--reason', 'upgrade'])
```

Or the functions themselves, after `offsite_backup.conf.configure(values)` with the setting
as a dict: `offsite_backup.run()`, `check_last_night()`, `freshness()`, `run_drill()`.
`configure(None)` goes back to Django's settings; `main()` does that itself when it returns.
