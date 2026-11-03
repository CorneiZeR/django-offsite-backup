# django-offsite-backup

Encrypted, immutable off-site backups of a Django project's **databases, media and
deployment configuration** — written by the machine they protect, readable only off it.

```python
OFFSITE_BACKUP = {
    'PREFIX': 'acme/prod',
    'STORE': {
        'BACKEND': 'offsite_backup.stores.s3.S3Store',
        'ENDPOINT_URL': os.environ.get('BACKUP_S3_ENDPOINT', ''),
        'BUCKET': os.environ.get('BACKUP_S3_BUCKET', ''),
        'ACCESS_KEY': os.environ.get('BACKUP_S3_ACCESS_KEY', ''),
        'SECRET_KEY': os.environ.get('BACKUP_S3_SECRET_KEY', ''),
    },
    'ENCRYPTION': {
        'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli',
        'RECIPIENTS': os.environ.get('BACKUP_AGE_RECIPIENT', ''),
    },
    'DATABASES': {'BACKEND': 'offsite_backup.databases.postgres.PostgresSource'},
}
```

```shell
python manage.py backup_offsite                    # a whole pass, now
python manage.py backup_offsite --without-media --reason deploy   # before a migration
python manage.py backup_check                      # the morning check
python manage.py backup_restore --stamp latest     # get it back
```

## What a pass does

1. Dumps every database with `pg_dump`, compresses it with `zstd`, encrypts it with
   [age](https://age-encryption.org) to a **public** key, and uploads it. A database that
   fails does not cost the others their night.
2. Packs the media into **volumes** of whole files (4 GiB by default), each a complete tar
   on its own, streamed through encryption one at a time — media larger than the free disk
   works.
3. Archives the deployment directory and the environment **rebuilt from a list of names**,
   last: a configuration object for a stamp means every part before it went up.
4. Only then rotates — counted in **whole UTC nights**, never in objects or age.
5. Says what happened in a chat (Telegram, or through your
   [django-aiogram](https://github.com/CorneiZeR/django-aiogram) bot), in one format a
   phone shows at a glance.

On S3 every object is uploaded with an Object Lock in **COMPLIANCE** mode, and the lock is
read back after the upload: an object locked less than asked fails the pass. A stolen
write key can neither read a backup (it has no private key) nor delete one (the lock
refuses it). **[Security model](Security-model.md)** spells out exactly what that does and
does not cover.

## Off, on, or refused

A machine with no store credentials and no recipient is **off**: every command says
"nothing to do" and exits 0, which is what a developer laptop wants. A machine that has
*some* of it is **partly configured** and is refused by name (`E001`) — a production
machine that lost one variable must not stop backing up in silence.
**[Settings](Settings.md#enabled-off-on-or-refused)** has the rule.

## Where to start

<div class="grid cards" markdown>

-   **[Installation](Installation.md)**

    ---

    The extras, the binaries the image needs, and a first pass in five minutes.

-   **[S3 providers](S3-providers.md)**

    ---

    Creating a bucket that can actually hold a locked backup — Object Lock is only
    possible at creation, and a missing default rule breaks every large object.

-   **[Deploy integration](Deploy-integration.md)**

    ---

    One command before the migration; non-zero stops the deploy while the live release
    keeps serving.

-   **[Restore](Restore.md)**

    ---

    Step by step, and the monthly drill that proves the backups are worth keeping.

</div>

## Requirements

Python 3.10–3.14 and Django 5.2+. The base install pulls Django and nothing else; each
driver is an extra (`[s3]`, `[telegram]`, `[aiogram]`). The binaries a pass runs — `pg_dump`,
`zstd`, `age` — are checked by `manage.py check` (`E003`) rather than discovered at 03:00.

Released under the MIT licence.
