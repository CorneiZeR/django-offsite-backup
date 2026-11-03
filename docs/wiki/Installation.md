# Installation

=== "S3 + Telegram"

    ```shell
    pip install 'django-offsite-backup[s3,telegram]'
    ```

    Backups in an S3 bucket with Object Lock, messages through the Telegram Bot API.

=== "S3 + django-aiogram"

    ```shell
    pip install 'django-offsite-backup[s3,aiogram]'
    ```

    Messages through the bot your project already runs with
    [django-aiogram](https://github.com/CorneiZeR/django-aiogram); no token in these
    settings.

=== "Directory only"

    ```shell
    pip install django-offsite-backup
    ```

    A local directory or Docker volume as the store, messages to the log. No driver at all
    — and no immutability either; see [Stores: directory](Stores-directory.md).

Requires Python 3.10–3.14 and Django 5.2+. Every pair in that range runs in CI, and an
integration job runs a whole cycle — backup, restore into a clean database, row counts of
every table compared — against PostgreSQL 16 and 18 and an S3 server with Object Lock.

## Extras

The base install is Django alone. Every backend is named by a dotted path in the settings,
imported only when a pass first needs it, and its driver is an extra:

| extra | pulls | for |
| --- | --- | --- |
| `s3` | `boto3>=1.34` | `offsite_backup.stores.s3.S3Store`, `offsite_backup.media.s3.S3Source` |
| `telegram` | `httpx2>=2.0` | `offsite_backup.notifiers.telegram.TelegramNotifier` |
| `aiogram` | `django-aiogram>=5.2` | `offsite_backup.notifiers.django_aiogram.TopicNotifier` |
| `all` | all of the above | |

Nothing is inferred from what happens to be installed. When a configured backend's driver
is missing, `manage.py check` says so with the install line (`E002`):

```text
?: (offsite_backup.E002) OFFSITE_BACKUP['STORE']['BACKEND'] is S3Store, but its driver is not installed.
    HINT: pip install "django-offsite-backup[s3]"
```

## Binaries

A pass streams through command-line tools rather than through Python memory. They are not
Python dependencies; install them in the image that runs the backup:

| binary | needed for | checked by |
| --- | --- | --- |
| `pg_dump` | `PostgresSource` — its major version must be **at least the server's** | `E003`, and `E004` under `check --deploy` |
| `zstd` | `COMPRESS` (on by default) and `DECOMPRESS` (restoring) — whatever program they name | `E003` |
| `age` | `AgeCli`, both for sealing and for restoring | `E003` |
| `psql`, `pg_restore` | restoring a database (`backup_restore --apply`) and verifying a custom-format dump; shipped with `pg_dump` in every PostgreSQL client package | `E003` |

`tar` is **not** needed: volumes and the configuration archive are written with Python's
`tarfile`. Measured on 613 MB in 2400 files, `tarfile` took 0.27 s against 0.72 s for the
`tar` CLI, and it is the only option for media streamed from S3, which has no files on disk
to hand to `tar`.

On Debian-based images:

```dockerfile
RUN apt-get update \
 && apt-get install -y --no-install-recommends age zstd postgresql-common \
 && /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y \
 && apt-get install -y --no-install-recommends postgresql-client-18 \
 && rm -rf /var/lib/apt/lists/*
```

Match `postgresql-client-<N>` to your server's major version, or newer: `pg_dump` refuses
to dump a server newer than itself.

## Add the app

```python
INSTALLED_APPS = [
    ...,
    'offsite_backup',
]
```

The app registers the [system checks](System-checks.md) and imports nothing else: no
backend, no driver, no network until a pass runs. It has no models and no migrations.

## A first pass

1. Make a key pair on **your** machine, not the server, and keep the private half in your
   password manager ([Encryption](Encryption.md)):

    ```shell
    age-keygen -o backup-acme-prod.key
    # Public key: age1...
    ```

2. Create the bucket **with Object Lock**, and a default COMPLIANCE rule of 13 days
   ([S3 providers](S3-providers.md)).

3. Put the settings in `settings.py`, reading every credential from the environment
   ([Settings](Settings.md)):

    ```python
    import os

    OFFSITE_BACKUP = {
        'PREFIX': 'acme/prod',
        'ENV': 'prod',
        'STORE': {
            'BACKEND': 'offsite_backup.stores.s3.S3Store',
            'ENDPOINT_URL': os.environ.get('BACKUP_S3_ENDPOINT', ''),
            'REGION': os.environ.get('BACKUP_S3_REGION', ''),
            'BUCKET': os.environ.get('BACKUP_S3_BUCKET', ''),
            'ACCESS_KEY': os.environ.get('BACKUP_S3_ACCESS_KEY', ''),
            'SECRET_KEY': os.environ.get('BACKUP_S3_SECRET_KEY', ''),
        },
        'ENCRYPTION': {
            'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli',
            'RECIPIENTS': os.environ.get('BACKUP_AGE_RECIPIENT', ''),
        },
        'DATABASES': {'BACKEND': 'offsite_backup.databases.postgres.PostgresSource'},
        'CONFIG': {
            'BACKEND': 'offsite_backup.config.paths.PathsSource',
            'PATHS': ['/app/deploy'],
        },
        'NOTIFY': [
            {
                'BACKEND': 'offsite_backup.notifiers.telegram.TelegramNotifier',
                'TOKEN': os.environ.get('BACKUP_TELEGRAM_TOKEN', ''),
                'CHAT_ID': os.environ.get('BACKUP_TELEGRAM_CHAT_ID', ''),
            },
        ],
    }
    ```

4. Check, then run:

    ```shell
    python manage.py check
    python manage.py check --deploy      # reaches the bucket and the database
    python manage.py backup_offsite
    # backup_offsite: 2 objects, 18734112 bytes up
    ```

5. Restore it once, now, before you need to — [Restore](Restore.md).

Then schedule the nightly pass and the morning check ([Scheduling](Scheduling.md)) and put
the deploy pass in front of your migrations ([Deploy integration](Deploy-integration.md)).
