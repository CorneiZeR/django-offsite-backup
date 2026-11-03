# System checks

The package registers two checks with Django's checks framework, both tagged
`offsite_backup`:

| check | runs on | reaches the network | codes |
| --- | --- | --- | --- |
| settings | every `manage.py` command that runs checks, and `manage.py check` | no | `E001`–`E003`, `E007`, `E008`, `W001`–`W005` |
| deploy | `manage.py check --deploy` only | yes — the bucket and each database | `E004`–`E006` |

**Nothing is checked on a machine where backups are off** ([Settings](Settings.md)). The
deploy check is also skipped while any `E001` is outstanding — fix the settings first.

```shell
python manage.py check                          # offline
python manage.py check --deploy                 # adds the bucket's lock and pg_dump's version
python manage.py check --tag offsite_backup     # this package's checks only
python manage.py check --deploy --fail-level WARNING
```

Codes are stable: a code never changes meaning. Each is explained, with its fix, on
[Troubleshooting](Troubleshooting.md).

## Errors

| code | when | where it comes from |
| --- | --- | --- |
| `offsite_backup.E001` | backups are configured by halves: `PREFIX`, a `STORE`/`ENCRYPTION` `BACKEND`, every source, or a backend's required option is empty; `KEEP_NIGHTS` is not a positive integer. Also: `FORMAT` not `plain`/`custom`, `VOLUME_BYTES` out of range, a Telegram `TOPIC_ID` that is not an integer, a `GUARD['CACHE']` missing from `CACHES` | settings |
| `offsite_backup.E002` | a `BACKEND` path that is empty, does not import, is not the family's base class — or names a shipped backend whose driver extra is not installed | settings |
| `offsite_backup.E003` | a binary a backend runs is not on `PATH`: `age`, `pg_dump`, `pg_restore`, `psql`, or the program `COMPRESS` / `DECOMPRESS` names | settings |
| `offsite_backup.E004` | `pg_dump` is older than a database server it must dump | deploy |
| `offsite_backup.E005` | the bucket cannot be read, is not versioned, has no Object Lock, or its lock configuration cannot be read | deploy |
| `offsite_backup.E006` | the bucket's default retention is missing, not COMPLIANCE, or not `LOCK_DAYS` days | deploy |
| `offsite_backup.E007` | `KEEP_NIGHTS` is not greater than `LOCK_DAYS` | settings |
| `offsite_backup.E008` | `RECIPIENTS` holds a private age key, or something that is not an age recipient | settings |

## Warnings

| code | when |
| --- | --- |
| `offsite_backup.W001` | the store is a `DirectoryStore` — no immutability, on the machine it protects |
| `offsite_backup.W002` | encryption is the test `Passthrough` — nothing is encrypted |
| `offsite_backup.W003` | the once-per-night guard uses a `LocMemCache` or `DummyCache` |
| `offsite_backup.W004` | a test run with `NOTIFY` set and notifiers not silenced |
| `offsite_backup.W005` | a media `ROOT`, a `CONFIG` path or the `RENDER_ENV` file does not exist here |

## Silencing

A warning you have decided to live with goes in `SILENCED_SYSTEM_CHECKS`:

```python
SILENCED_SYSTEM_CHECKS = ['offsite_backup.W001']  # a directory store on staging, on purpose
```

Do not silence an error to get a deploy through: every one of them is a backup that would
fail, or one that would succeed while protecting nothing.

## In CI

Run `manage.py check --deploy` with the production settings in the image you are about
to deploy — it is where `E003` (a binary missing from a slimmed image) and `E004` (a
`pg_dump` older than the server after a database upgrade) are cheapest to find. Mind what
it cannot see: without the store credentials the machine counts as off and nothing at all
is checked, and a database it cannot connect to is skipped by `E004` rather than
reported.
