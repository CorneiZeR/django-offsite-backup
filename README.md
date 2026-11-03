# django-offsite-backup

[![CI](https://github.com/CorneiZeR/django-offsite-backup/actions/workflows/ci.yml/badge.svg)](https://github.com/CorneiZeR/django-offsite-backup/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/django-offsite-backup.svg)](https://pypi.org/project/django-offsite-backup/)
[![Python](https://img.shields.io/pypi/pyversions/django-offsite-backup.svg)](https://pypi.org/project/django-offsite-backup/)

Off-site backups of a Django project's **databases, media and deployment configuration**:
compressed, encrypted to a public key the server holds (so the server can write a backup
and cannot read one), uploaded to S3 with **Object Lock COMPLIANCE** read back on every
object, and rotated by **whole nights** — never by age alone, never by object count.

- PostgreSQL (one database, a list, or a list computed at run time), plain or custom format
- media from a directory, a Docker volume or an S3 bucket, in volumes of whole files
- the deployment directory plus the environment rebuilt from a list of variable names
- `age` encryption to one or more recipients; `zstd` compression before it
- a pass before every deploy without media (fast, and never mistaken for a night)
- a once-per-night guard, and a morning check that notices the night that never ran
- every outcome said in Telegram (or through django-aiogram, or a log), in one glanceable format
- restore that downloads, decrypts, verifies — and, on request, loads a database
- system checks that refuse a half-configured machine by name and a bucket without a lock

Every backend is a dotted path in settings and imported only when named; each driver is an
extra. A base install pulls Django and nothing else.

## 30-second setup

```shell
pip install "django-offsite-backup[s3,telegram]"
```

```python
import os

INSTALLED_APPS = [..., 'offsite_backup']

OFFSITE_BACKUP = {
    'PREFIX': 'acme/prod',
    'ENV': 'prod',
    'STORE': {
        'BACKEND': 'offsite_backup.stores.s3.S3Store',
        'ENDPOINT_URL': os.environ.get('BACKUP_S3_ENDPOINT_URL', ''),
        'BUCKET': os.environ.get('BACKUP_S3_BUCKET', ''),
        'ACCESS_KEY': os.environ.get('BACKUP_S3_ACCESS_KEY', ''),
        'SECRET_KEY': os.environ.get('BACKUP_S3_SECRET_KEY', ''),
        'LOCK_DAYS': 13,
    },
    'ENCRYPTION': {
        'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli',
        'RECIPIENTS': [os.environ.get('BACKUP_AGE_RECIPIENT', '')],
    },
    'DATABASES': {'BACKEND': 'offsite_backup.databases.postgres.PostgresSource'},
    'MEDIA': [{'BACKEND': 'offsite_backup.media.filesystem.DirectorySource'}],
    'CONFIG': {'BACKEND': 'offsite_backup.config.paths.PathsSource', 'PATHS': ['/app/deploy']},
    'RETENTION': {'KEEP_NIGHTS': 14},
    'NOTIFY': [
        {
            'BACKEND': 'offsite_backup.notifiers.telegram.TelegramNotifier',
            'TOKEN': os.environ.get('BACKUP_TG_TOKEN', ''),
            'CHAT_ID': os.environ.get('BACKUP_TG_CHAT_ID', ''),
        }
    ],
}
```

With every credential empty (a development machine) backups are **off** and the commands
say "nothing to do". With some set and some missing, `manage.py check` names each one.

```shell
python manage.py check --deploy                                  # bucket lock, pg_dump version
python manage.py backup_offsite                                  # a pass now
python manage.py backup_offsite --without-media --reason deploy  # before a migration
python manage.py backup_check                                    # the morning check
python manage.py backup_list
python manage.py backup_restore --stamp latest --identity key.txt --into ./restore
```

The image needs `pg_dump` (at least the server's major), `zstd` and `age`. The bucket must be
created **with Object Lock** and a default COMPLIANCE rule of `LOCK_DAYS` days.

## Documentation

**<https://corneizer.github.io/django-offsite-backup/>** — installation, every setting,
S3 providers, retention and locks, restore drills, the security model, troubleshooting by
check code, and a page for AI assistants wiring the package into a project.

## License

MIT
