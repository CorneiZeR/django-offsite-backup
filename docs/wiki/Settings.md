# Settings

One dict, `OFFSITE_BACKUP`, in `settings.py`. Every key has a default; the backends each
take a block of their own — `BACKEND`, a dotted path, plus that backend's options.

```python
import os

OFFSITE_BACKUP = {
    'PREFIX': 'acme/prod',
    'ENV': 'prod',
    'NAME': 'acme',
    'STORE': {
        'BACKEND': 'offsite_backup.stores.s3.S3Store',
        'ENDPOINT_URL': os.environ.get('BACKUP_S3_ENDPOINT', ''),
        'REGION': os.environ.get('BACKUP_S3_REGION', ''),
        'BUCKET': os.environ.get('BACKUP_S3_BUCKET', ''),
        'ACCESS_KEY': os.environ.get('BACKUP_S3_ACCESS_KEY', ''),
        'SECRET_KEY': os.environ.get('BACKUP_S3_SECRET_KEY', ''),
        'LOCK_DAYS': 13,
    },
    'ENCRYPTION': {
        'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli',
        'RECIPIENTS': os.environ.get('BACKUP_AGE_RECIPIENT', ''),
    },
    'DATABASES': {
        'BACKEND': 'offsite_backup.databases.postgres.PostgresSource',
        'ALIASES': ['default'],
        'FORMAT': 'plain',
    },
    'MEDIA': [
        {'BACKEND': 'offsite_backup.media.filesystem.DirectorySource', 'EXCLUDE': ['thumbs/*']},
    ],
    'CONFIG': {
        'BACKEND': 'offsite_backup.config.paths.PathsSource',
        'PATHS': ['/app/deploy'],
        'RENDER_ENV': 'deploy-vars.txt',
    },
    'RETENTION': {'KEEP_NIGHTS': 14},
    'CHECK': {'STALE_HOURS': 26},
    'NOTIFY': [
        {
            'BACKEND': 'offsite_backup.notifiers.telegram.TelegramNotifier',
            'TOKEN': os.environ.get('BACKUP_TELEGRAM_TOKEN', ''),
            'CHAT_ID': os.environ.get('BACKUP_TELEGRAM_CHAT_ID', ''),
            'TOPIC_ID': os.environ.get('BACKUP_TELEGRAM_TOPIC_ID', ''),
        },
    ],
    'GUARD': {'CACHE': 'default'},
}
```

**Credentials come from the environment, in your settings.** The package never reads
`os.environ` for its configuration (the one exception is `AGE_IDENTITY`, the private key
a *restore* reads — see [Restore](Restore.md)). Read each one with a default of `''`, as
above: an unset variable then means "off", not a `KeyError` at import.

## `ENABLED`: off, on, or refused

Three states, never confused:

* **off** — `OFFSITE_BACKUP` is absent or empty, `ENABLED` is false, or none of the
  options that name a destination is set. Commands print "not configured on this machine,
  nothing to do" and exit 0; the system checks say nothing. A development machine holds no
  key — not giving it one *is* the enforcement.
* **partly configured** — something says "back up" and something it needs is empty. Every
  missing piece is named by `E001`, and a pass refuses to start. It is never read as
  "off": a production machine that lost one variable must not stop backing up in silence.
* **on** — every backend builds and every required option is set.

With `ENABLED` left at `None` (the default) the machine is on when **any** of these is
non-empty:

| backend | options that switch it on |
| --- | --- |
| `S3Store` | `BUCKET`, `ACCESS_KEY`, `SECRET_KEY`, `ENDPOINT_URL` |
| `DirectoryStore` | `ROOT` |

A recipient is not among them: it is a public key, and writing it into settings must not
switch on every machine that has no store key.

A `BACKEND` path that does not import also counts as "on", so `E002` can say what is
wrong with it. `ENABLED: True` or `False` overrides the rule either way.

!!! warning "A recipient hard-coded in settings switches every machine on"

    A public key is not a secret, so it is tempting to write `RECIPIENTS: ['age1...']`
    straight into `settings.py`. That makes every machine that loads those settings —
    developer laptops and CI included — *partly configured*, and `manage.py check` reports
    `E001` everywhere. Read the recipient from the environment like the credentials, or
    set `ENABLED` from an environment variable of its own.

## Top-level keys

Every key of `offsite_backup.conf.DEFAULTS`. `RETENTION`, `CHECK` and `GUARD` merge with
their defaults key by key, so `'GUARD': {'CACHE': 'redis'}` keeps `RUNNING_SECONDS`; every
other key replaces its default whole.

| key | type | default | what it is | validated by |
| --- | --- | --- | --- | --- |
| `ENABLED` | `bool` or `None` | `None` | on/off; `None` decides from the options above | — |
| `PREFIX` | `str` | `''` | every key lives under `<PREFIX>/`; rotation never leaves it. Surrounding `/` are stripped. Required | `E001` |
| `ENV` | `str` | `''` | the environment, for the message's hashtag (`#prod`) | — |
| `NAME` | `str` | `''` | a service name; shown in a message only when `PREFIX` is empty | — |
| `STORE` | block | `None` | where backups are kept — [S3](Stores-S3.md) or [a directory](Stores-directory.md). Required | `E001`, `E002` |
| `ENCRYPTION` | block | `None` | how they are sealed — [Encryption](Encryption.md). Required | `E001`, `E002`, `E008` |
| `DATABASES` | block | `None` | [Databases](Databases.md) | `E001`, `E002` |
| `MEDIA` | list of blocks (or one block) | `[]` | [Media](Media.md) | `E001`, `E002`, `W005` |
| `CONFIG` | block | `None` | [Configuration archive](Configuration-archive.md) | `E002`, `W005` |
| `COMPRESS` | list of `str` (argv) | `['zstd', '-19', '--long=27', '-T0', '-q']` | the compression stage for databases and the configuration archive; empty turns compression off | `E003` (`zstd` on `PATH` when non-empty) |
| `DECOMPRESS` | list of `str` (argv) | `['zstd', '-d', '-q', '--long=31']` | the stage a restore runs on anything that starts with zstd's magic bytes | — |
| `RETENTION` | dict | `{'KEEP_NIGHTS': 14}` | [Retention](Retention.md) | `E001`, `E007` |
| `CHECK` | dict | `{'STALE_HOURS': 26}` | [Monitoring](Monitoring.md) | — |
| `NOTIFY` | list of blocks | `[]` | [Notifications](Notifications.md) | `E002`, `W004` |
| `MESSAGES` | dotted path | `'offsite_backup.messages.default'` | the formatter — [Notifications](Notifications.md#your-own-format) | — |
| `GUARD` | dict | see below | the once-per-night claim — [Scheduling](Scheduling.md#once-per-night) | `E001`, `W003` |
| `LAST_SUCCESS_FILE` | path | `''` | a file rewritten with the ISO time of every successful pass — [Monitoring](Monitoring.md) | — |
| `WORKDIR` | path | `''` | where the temporary workspace is made; `''` is the system's temporary directory | — |

At least one of `DATABASES`, `MEDIA` and `CONFIG` must be set, or `E001` says
"nothing to back up".

### `RETENTION`

| key | type | default | |
| --- | --- | --- | --- |
| `KEEP_NIGHTS` | positive `int` | `14` | whole UTC nights kept per kind. Must be greater than the store's `LOCK_DAYS` (`E007`); not a positive integer is `E001` |

### `CHECK`

| key | type | default | |
| --- | --- | --- | --- |
| `STALE_HOURS` | number | `26` | how old the newest whole backup may be before the morning check calls it missing |

### `GUARD`

| key | type | default | |
| --- | --- | --- | --- |
| `ENABLED` | `bool` | `True` | whether `run_nightly()` claims the night at all |
| `CACHE` | cache alias | `'default'` | must exist in `CACHES` (`E001`) and be shared between workers (`W003` on `LocMemCache` and `DummyCache`) |
| `RUNNING_SECONDS` | `int` | `3600` | how long a pass whose worker died holds the night |
| `DONE_SECONDS` | `int` | `93600` (26 h) | how long "done" is remembered — past the end of its UTC date |

## Backend options

Every backend reads its own block: `BACKEND` plus the options below. An option listed as
**required** is named by `E001` when empty on a machine where backups are on.

### `STORE`: `offsite_backup.stores.s3.S3Store`

Needs the `[s3]` extra. [Stores: S3](Stores-S3.md).

| option | type | default | |
| --- | --- | --- | --- |
| `BUCKET` | `str` | — | **required** |
| `ACCESS_KEY` | `str` | — | **required** |
| `SECRET_KEY` | `str` | — | **required** |
| `ENDPOINT_URL` | URL | `None` (AWS) | the provider's endpoint, e.g. `https://fsn1.your-objectstorage.com` |
| `REGION` | `str` | `None` | the region name the provider expects |
| `LOCK_DAYS` | `int` | `13` | days every object is locked COMPLIANCE; `0` stores without a lock. Checked against the bucket's default rule (`E006`) and against `KEEP_NIGHTS` (`E007`) |

Only these options make the client: no `~/.aws`, no instance role, no `AWS_*` variables.

### `STORE`: `offsite_backup.stores.filesystem.DirectoryStore`

[Stores: directory](Stores-directory.md). Always raises `W001`.

| option | type | default | |
| --- | --- | --- | --- |
| `ROOT` | path | — | **required**; `~` is expanded |

### `ENCRYPTION`: `offsite_backup.encryption.age_cli.AgeCli`

[Encryption](Encryption.md). Runs `age` (`E003`).

| option | type | default | |
| --- | --- | --- | --- |
| `RECIPIENTS` | `str` or list of `str` | — | **required**; public keys (`age1…`, `ssh-ed25519 …`, `ssh-rsa …`). A private key or anything else is `E008` |

### `DATABASES`: `offsite_backup.databases.postgres.PostgresSource`

[Databases](Databases.md). Runs `pg_dump` (`E003`, `E004`).

| option | type | default | |
| --- | --- | --- | --- |
| `ALIASES` | list of aliases, or a dotted path to a callable returning one | `['default']` | which `DATABASES` aliases to dump |
| `FORMAT` | `'plain'` or `'custom'` | `'plain'` | `pg_dump` output format; anything else is `E001` |
| `NO_OWNER` | `bool` | `True` | adds `--no-owner --no-privileges` |
| `EXTRA_ARGS` | list of `str` | `[]` | appended to the `pg_dump` command |

The connection comes from Django's `DATABASES[alias]`: `HOST`, `PORT`, `USER`, `NAME`,
`PASSWORD` (passed in `PGPASSWORD`, never on the command line) and
`OPTIONS['sslmode']` / `OPTIONS['sslrootcert']`.

### `MEDIA`: options every media source takes

[Media](Media.md).

| option | type | default | |
| --- | --- | --- | --- |
| `NAME` | `str` | `''` | the kind: `media/` when empty, `media-<name>/` otherwise. Give each of several sources its own |
| `VOLUME_BYTES` | `int` | `4294967296` (4 GiB) | the most one volume holds; must be more than 20 KiB and at most 5 GB (`E001`) |
| `INCLUDE` | list of globs | `[]` | only names matching one of these |
| `EXCLUDE` | list of globs | `[]` | names matching one of these are left out |
| `COMPRESS` | `bool` | `False` | run the `COMPRESS` stage on volumes too |

### `MEDIA`: `offsite_backup.media.filesystem.DirectorySource`

| option | type | default | |
| --- | --- | --- | --- |
| `ROOT` | path | `settings.MEDIA_ROOT` | the directory archived; missing is `W005` |

### `MEDIA`: `offsite_backup.media.s3.S3Source`

Needs the `[s3]` extra. Its credentials are its own; it does not borrow the store's.

| option | type | default | |
| --- | --- | --- | --- |
| `BUCKET` | `str` | — | **required** |
| `PREFIX` | `str` | `''` | only keys under it |
| `ENDPOINT_URL` | URL | `None` | |
| `REGION` | `str` | `None` | |
| `ACCESS_KEY` | `str` | `None` | |
| `SECRET_KEY` | `str` | `None` | |

### `CONFIG`: `offsite_backup.config.paths.PathsSource`

[Configuration archive](Configuration-archive.md).

| option | type | default | |
| --- | --- | --- | --- |
| `PATHS` | list of paths | `[]` | files and directories archived; missing is `W005` |
| `RENDER_ENV` | path | `None` | a file of variable names to rebuild the environment from; relative to `settings.BASE_DIR`; missing is `W005` |
| `EXCLUDE` | list of globs | `[]` | matched against each file's **name**; always in addition to `.env`, which never travels |

### `NOTIFY`: `offsite_backup.notifiers.telegram.TelegramNotifier`

Needs the `[telegram]` extra. [Notifications](Notifications.md).

| option | type | default | |
| --- | --- | --- | --- |
| `TOKEN` | `str` | — | **required** (see the note below) |
| `CHAT_ID` | `int` or `str` | — | **required** |
| `TOPIC_ID` | `int` | `None` | a forum topic (`message_thread_id`); not an integer is `E001` |
| `TIMEOUT` | seconds | `10` | |
| `API_URL` | URL | `'https://api.telegram.org'` | for a local Bot API server |

### `NOTIFY`: `offsite_backup.notifiers.django_aiogram.TopicNotifier`

Needs the `[aiogram]` extra.

| option | type | default | |
| --- | --- | --- | --- |
| `CHAT_ID` | `int` or `str` | — | **required** |
| `TOPIC_ID` | `int` | `None` | a forum topic |
| `BOT` | alias | `None` | one of django-aiogram's bots; the default bot when absent |

### `NOTIFY`: `offsite_backup.notifiers.log.LogNotifier`

No options. Writes to the `offsite_backup` logger.

A notifier's required options are named by `E001` like any other backend's: an empty
`TOKEN` or `CHAT_ID` on a machine where backups are on fails `manage.py check`.

## Your own backend

Every family has a base class: `offsite_backup.stores.base.Store`,
`offsite_backup.encryption.base.Sealer`, `offsite_backup.databases.base.DatabaseSource`,
`offsite_backup.media.base.MediaSource`, `offsite_backup.config.base.ConfigSource`,
`offsite_backup.notifiers.base.Notifier`. Name your subclass by its dotted path in
`BACKEND`; the package needs no change. Declare `REQUIRED`, `SIGNALS` and `BINARIES` on the
class to take part in `E001`, the on/off decision and `E003`, and override `check()` (offline)
and `deploy_check()` (network, run by `check --deploy`) for anything else. A path that is
not a subclass of the family's base is `E002`.
