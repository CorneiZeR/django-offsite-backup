# Settings

One dict, `OFFSITE_BACKUP`, in `settings.py` — or, on a server with no Django project, the
same keys at the top level of a TOML file ([Without Django](Plain-servers.md#the-setting)).
Every key has a default; the backends each take a block of their own — `BACKEND`, a dotted
path, plus that backend's options.

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
`os.environ` for its configuration (the exceptions are `AGE_IDENTITY`, the private key
a *restore* reads — see [Restore](Restore.md) — and `OFFSITE_BACKUP_SILENCE`, the test
runner's switch — see [Testing](Testing.md#parallel-workers); off Django, the command line
expands `${NAME}` in its TOML file — see [Without Django](Plain-servers.md#the-setting)). Read each one with a default of `''`, as
above: an unset variable then means "off", not a `KeyError` at import.

## Blank means unset

What `os.environ.get('X', '')` gives on a machine without `X` is read as *not set*, wherever
it ends up:

* `None`, a blank string and `0` (what `int(os.environ.get('X') or 0)` gives) are unset. A
  required option holding one of them is named by `E001`, like an absent one.
* A list made only of those — `[os.environ.get('X', '')]` is `['']` — is unset too.
* Inside a list, blank items are dropped: `PATHS`, `INCLUDE`/`EXCLUDE`, a list of
  `ALIASES`, `RECIPIENTS`.
* A `TOPIC_ID` that is unset means the chat's main thread.

So the plain idiom works as it is, for one recipient or several:

```python
'ENCRYPTION': {
    'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli',
    # 'age1abc...', or 'age1abc..., age1def...' — or nothing, on a laptop
    'RECIPIENTS': os.environ.get('BACKUP_AGE_RECIPIENTS', ''),
},
```

`RECIPIENTS` takes a string or a list. A string is split on commas and newlines, and — for
`age1…` keys only — on whitespace; an `ssh-ed25519 …` recipient keeps the space inside it.
On a machine with the store credentials and no recipient, `E001` names `RECIPIENTS`.

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
switch on every machine that has no store key. **A machine with only the age recipient set
— and no store credentials — is off, not partly configured.** A hand-written predecessor
may have used the recipient as its "on" switch; here the store is the switch.

A `BACKEND` path that does not import also counts as "on", so `E002` can say what is
wrong with it. `ENABLED: True` or `False` overrides the rule either way.

!!! note "A recipient alone is off"

    A public key is not a secret, so `RECIPIENTS: ['age1...']` may be written straight into
    `settings.py`. It switches nothing on: developer laptops and CI that load those settings
    without store credentials stay **off**, and `manage.py check` says nothing there. The
    other way round — store credentials and no recipient — is partly configured, and `E001`
    names `RECIPIENTS`.

## Top-level keys

Every key of `offsite_backup.conf.DEFAULTS`. `RETENTION`, `CHECK`, `GUARD` and `DRILL` merge with
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
| `MEDIA` | list of blocks (or one block) | `[]` | [Media](Media.md) | `E001`, `E002`, `W005` (deploy) |
| `CONFIG` | block | `None` | [Configuration archive](Configuration-archive.md) | `E002`, `E009`, `W005` (deploy) |
| `COMPRESS` | list of `str` (argv) | `['zstd', '-19', '--long=27', '-T0', '-q']` | the compression stage for databases and the configuration archive; empty turns compression off | `E003` (`zstd` on `PATH` when non-empty) |
| `DECOMPRESS` | list of `str` (argv) | `['zstd', '-d', '-q', '--long=31']` | the stage a restore runs on anything that starts with zstd's magic bytes | — |
| `RETENTION` | dict | `{'KEEP_NIGHTS': 14}` | [Retention](Retention.md) | `E001`, `E007` |
| `CHECK` | dict | `{'STALE_HOURS': 26}` | [Monitoring](Monitoring.md) | — |
| `NOTIFY` | list of blocks | `[]` | [Notifications](Notifications.md) | `E002`, `W004` |
| `MESSAGES` | dotted path | `'offsite_backup.messages.default'` | the formatter — [Notifications](Notifications.md#your-own-format) | — |
| `GUARD` | dict | see below | the once-per-night claim — [Scheduling](Scheduling.md#once-per-night) | `E001`, `W003` |
| `LAST_SUCCESS_FILE` | path | `''` | a file rewritten with the ISO time of every successful pass — [Monitoring](Monitoring.md) | — |
| `DEFAULT_REASON` | `str` | `'manual'` | the reason `backup_offsite` uses when `--reason` is not given — [Deploy integration](Deploy-integration.md) | — |
| `STRICT` | `bool` | `False` | every `CONFIG` path and every media source is required, and an unreadable file fails the pass — [below](#strict-and-required) | `E009` |
| `WORKDIR` | path | `''` | where the temporary workspace is made — a pass's, a restore's, a drill's; `''` is the system's temporary directory | — |
| `DRILL` | dict | see below | the restore drill — [Restore](Restore.md#the-monthly-drill) | `E011` (deploy) |

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
| `BACKEND` | `'cache'` or `'database'` | `'cache'` | where the night is claimed: a shared Django cache, or a PostgreSQL advisory lock ([Scheduling](Scheduling.md#once-per-night)); anything else is `E001` |
| `CACHE` | cache alias | `'default'` | `cache` backend: must exist in `CACHES` (`E001`) and be shared between workers (`W003` on `LocMemCache` and `DummyCache`) |
| `RUNNING_SECONDS` | `int` | `3600` | `cache` backend: how long a pass whose worker died holds the night |
| `DONE_SECONDS` | `int` | `93600` (26 h) | `cache` backend: how long "done" is remembered — past the end of its UTC date |
| `DATABASE` | database alias | `'default'` | `database` backend: the database the lock is taken on; an alias that is not a PostgreSQL database in `DATABASES` is `E001` |

### `DRILL`

| key | type | default | |
| --- | --- | --- | --- |
| `DATABASE` | database alias | `''` | the throwaway database every dump is restored into — an alias in `DATABASES` (or the source's `CONNECTIONS`). Required for a drill of a backup that holds databases. The same alias as a live one, or one that reaches the same host, port and database name, is refused — and is `E011` under `check --deploy`, as is an alias that names no database |
| `IDENTITY_FILE` | path | `''` | the age identity the drill decrypts with, when `--identity` is not given; `''` falls back to `AGE_IDENTITY` |
| `MEDIA` | `bool` | `True` | decrypt and list every media volume too (`--without-media` turns it off for one run) |
| `COMPARE_LIVE` | `bool` | `True` | count the live databases too, and show both figures |

### `STRICT` and `REQUIRED`

By default a pass backs up what is there: a missing `CONFIG` path is logged and left out,
an unreadable file is logged and left out, and a media source with no files writes no volume.
A deployment whose certificates or signing keys are part of what restores the machine may
want a pass without them to fail instead. Two settings, one mechanism:

* `REQUIRED` on a source, for that source only:
    * on `CONFIG`, `True` makes every path in `PATHS` required, and a list names the required
      ones (`'REQUIRED': ['/app/deploy/certs']`). A required path missing at pass time fails
      the pass;
    * on a media source, `True` makes a source with no files at all a failure.
* `STRICT: True` at the top level, for the whole pass: every `CONFIG` path and every media
  source is required, and a file that cannot be read — a `CONFIG` file, or a file under a
  `DirectorySource` `ROOT` — fails the pass instead of being left out.

The pass fails with `SourceMissingError`, and the FAILED message names what was missing:

```text
SourceMissingError: OFFSITE_BACKUP['CONFIG']: required path(s) missing: /app/deploy/certs
```

`manage.py check --deploy` reports a required `CONFIG` path missing where it runs as `E009`,
and an optional one as `W005`; a plain `manage.py check` says nothing about paths
([System checks](System-checks.md#why-the-path-checks-are-deploy-checks)).

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
| `ADDRESSING_STYLE` | `'auto'`, `'path'` or `'virtual'` | `'auto'` | botocore's addressing style: `path` is `https://host/bucket/key`, `virtual` is `https://bucket.host/key`. MinIO and SeaweedFS need `path` ([S3 providers](S3-providers.md#addressing-style)); anything else is `E001` |
| `SIGNATURE_VERSION` | `str` | `None` | botocore's signature version (e.g. `'s3v4'`); `None` keeps botocore's default |
| `VERIFY` | `bool` or path | `True` | TLS verification: `True`, `False`, or the path of a CA bundle for a store behind an internal certificate. A path that is not a file is `E001` |

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
| `RECIPIENTS` | `str` or list of `str` | — | **required**; public keys (`age1…`, `ssh-ed25519 …`, `ssh-rsa …`). A string splits on commas, newlines and — between `age1…` keys — whitespace ([above](#blank-means-unset)). A private key or anything else is `E008` |

### `DATABASES`: `offsite_backup.databases.postgres.PostgresSource`

[Databases](Databases.md). Runs `pg_dump` (`E003`, `E004`).

| option | type | default | |
| --- | --- | --- | --- |
| `ALIASES` | list of aliases, or a dotted path to a callable returning one | `['default']` | which `DATABASES` aliases to dump; blank items are dropped |
| `FORMAT` | `'plain'` or `'custom'` | `'plain'` | `pg_dump` output format; anything else is `E001` |
| `NO_OWNER` | `bool` | `True` | adds `--no-owner --no-privileges` |
| `EXTRA_ARGS` | list of `str` | `[]` | appended to the `pg_dump` command |
| `CONNECTIONS` | dict of alias → connection block | `None` | the connections themselves, read instead of Django's `DATABASES` — what a server with no Django project uses ([Databases](Databases.md#the-connection)); not a mapping of blocks is `E001` |

The connection comes from `CONNECTIONS[alias]` when it is set, from Django's
`DATABASES[alias]` otherwise: `HOST`, `PORT`, `USER`, `NAME`,
`PASSWORD` (passed in `PGPASSWORD`, never on the command line) and
`OPTIONS['sslmode']` / `OPTIONS['sslrootcert']`.

### `MEDIA`: options every media source takes

[Media](Media.md).

| option | type | default | |
| --- | --- | --- | --- |
| `NAME` | `str` | `''` | the kind: `media/` when empty, `media-<name>/` otherwise. Give each of several sources its own |
| `VOLUME_BYTES` | `int` | `4294967296` (4 GiB) | the most one volume holds; must be more than 20 KiB and at most 5 GB (`E001`) |
| `INCLUDE` | list of globs | `[]` | only names matching one of these; blank items are dropped |
| `EXCLUDE` | list of globs | `[]` | names matching one of these are left out; blank items are dropped |
| `COMPRESS` | `bool` | `False` | run the `COMPRESS` stage on volumes too |
| `REQUIRED` | `bool` | `False` | a source with no files fails the pass ([above](#strict-and-required)) |

### `MEDIA`: `offsite_backup.media.filesystem.DirectorySource`

| option | type | default | |
| --- | --- | --- | --- |
| `ROOT` | path | `settings.MEDIA_ROOT` | the directory archived; missing is `W005` under `check --deploy`, and logged by the pass. Under `STRICT` a file in it that cannot be read fails the pass |

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
| `ADDRESSING_STYLE` | `'auto'`, `'path'` or `'virtual'` | `'auto'` | as on `S3Store`; anything else is `E001` |
| `SIGNATURE_VERSION` | `str` | `None` | as on `S3Store` |
| `VERIFY` | `bool` or path | `True` | as on `S3Store`; a path that is not a file is `E001` |

### `CONFIG`: `offsite_backup.config.paths.PathsSource`

[Configuration archive](Configuration-archive.md).

| option | type | default | |
| --- | --- | --- | --- |
| `PATHS` | list of paths | `[]` | files and directories archived; blank items are dropped; missing is `W005` under `check --deploy`, and logged by the pass |
| `RENDER_ENV` | path | `None` | a file of variable names to rebuild the environment from; relative to `settings.BASE_DIR`; missing is `W005` under `check --deploy`, and logged by the pass |
| `EXCLUDE` | list of globs | `[]` | matched against each file's **name**; always in addition to `.env`, which never travels |
| `REQUIRED` | `bool` or list of paths | `False` | `True`: every path is required; a list: those paths are. A required path missing at pass time fails the pass, and is `E009` under `check --deploy` ([above](#strict-and-required)) |

### `NOTIFY`: `offsite_backup.notifiers.telegram.TelegramNotifier`

Needs the `[telegram]` extra. [Notifications](Notifications.md).

| option | type | default | |
| --- | --- | --- | --- |
| `TOKEN` | `str` | — | **required** (see the note below) |
| `CHAT_ID` | `int` or `str` | — | **required**; `0` counts as unset |
| `TOPIC_ID` | `int` | `None` | a forum topic (`message_thread_id`); `None`, blank or `0` is the main thread; not an integer is `E010` |
| `TIMEOUT` | seconds | `10` | |
| `API_URL` | URL | `'https://api.telegram.org'` | for a local Bot API server |

### `NOTIFY`: `offsite_backup.notifiers.django_aiogram.TopicNotifier`

Needs the `[aiogram]` extra.

| option | type | default | |
| --- | --- | --- | --- |
| `CHAT_ID` | `int` or `str` | — | **required**; `0` counts as unset |
| `TOPIC_ID` | `int` | `None` | a forum topic; `None`, blank or `0` is the main thread; not an integer is `E010` |
| `DELIVERY` | `'direct'` or `'queue'` | `'direct'` | `direct` calls `bot.send_raw` from the process running the pass, which then needs the bot token; `queue` calls `bot.send`, handing the message to the queue a bot container consumes ([Notifications](Notifications.md#direct-or-queued)); anything else is `E010` |
| `BOT` | alias | `None` | one of django-aiogram's bots; the default bot when absent |
| `BOT_FACTORY` | dotted path | `None` | a callable returning an object with `send_raw` / `send`, used instead of `BOT` — another bot wrapper, or an older django-aiogram; one that does not import or is not callable is `E002` |

### `NOTIFY`: `offsite_backup.notifiers.webhook.WebhookNotifier`

Needs the `[webhook]` extra. [Notifications](Notifications.md#a-webhook).

| option | type | default | |
| --- | --- | --- | --- |
| `URL` | URL | — | **required**; `http://` or `https://`, else `E010` |
| `SECRET` | `str` | `''` | signs every body: `X-Offsite-Backup-Signature: sha256=<HMAC-SHA256>` |
| `HEADERS` | dict of `str` | `{}` | extra request headers; not a mapping is `E010` |
| `TIMEOUT` | seconds | `10` | per attempt; not a positive number is `E010` |
| `RETRIES` | `int` | `2` | attempts after the first on a network error, a 429 or a 5xx; not a whole number ≥ 0 is `E010` |

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
