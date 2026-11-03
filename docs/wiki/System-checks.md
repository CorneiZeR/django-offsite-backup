# System checks

The package registers two checks with Django's checks framework, both tagged
`offsite_backup`:

| check | runs on | reaches the network | codes |
| --- | --- | --- | --- |
| settings | every `manage.py` command that runs checks, and `manage.py check` | no | `E001`–`E003`, `E007`, `E008`, `W001`–`W005` |
| deploy | `manage.py check --deploy` only | yes — the bucket and each database | `E004`–`E006`, `E009` |

**Nothing is checked on a machine where backups are off** ([Settings](Settings.md)). The
deploy check is also skipped while any `E001` is outstanding — fix the settings first.

```shell
python manage.py check                          # offline
python manage.py check --deploy                 # adds the bucket's lock, pg_dump's version, required paths
python manage.py check --tag offsite_backup     # this package's checks only
python manage.py check --deploy --fail-level WARNING
```

Codes are stable: a code never changes meaning. Each is explained, with its fix, on
[Troubleshooting](Troubleshooting.md).

## Errors

| code | when | where it comes from |
| --- | --- | --- |
| `offsite_backup.E001` | backups are configured by halves: `PREFIX`, a `STORE`/`ENCRYPTION` `BACKEND`, every source, or a backend's required option is unset — `None`, blank, `0`, or a list of only those counts as unset ([Settings](Settings.md#blank-means-unset)); `KEEP_NIGHTS` is not a positive integer. Also: `FORMAT` not `plain`/`custom`, `VOLUME_BYTES` out of range, a `TOPIC_ID` that is not an integer (either Telegram notifier), a `DELIVERY` other than `direct`/`queue`, an `ADDRESSING_STYLE` other than `auto`/`path`/`virtual`, a `VERIFY` path that is not a file, a `GUARD['BACKEND']` other than `cache`/`database`, a `GUARD['CACHE']` missing from `CACHES`, a `GUARD['DATABASE']` that is not a PostgreSQL database | settings |
| `offsite_backup.E002` | a `BACKEND` path that is empty, does not import, is not the family's base class — or names a shipped backend whose driver extra is not installed; a `TopicNotifier` `BOT_FACTORY` that does not import or is not callable | settings |
| `offsite_backup.E003` | a binary a backend runs is not on `PATH`: `age`, `pg_dump`, `pg_restore`, `psql`, or the program `COMPRESS` / `DECOMPRESS` names | settings |
| `offsite_backup.E004` | `pg_dump` is older than a database server it must dump | deploy |
| `offsite_backup.E005` | the bucket cannot be read, is not versioned, has no Object Lock, or its lock configuration cannot be read | deploy |
| `offsite_backup.E006` | the bucket's default retention is missing, not COMPLIANCE, or not `LOCK_DAYS` days | deploy |
| `offsite_backup.E007` | `KEEP_NIGHTS` is not greater than `LOCK_DAYS` | settings |
| `offsite_backup.E008` | `RECIPIENTS` holds a private age key, or something that is not an age recipient | settings |
| `offsite_backup.E009` | a required `CONFIG` path (`REQUIRED`, or `STRICT`) does not exist where the check runs | deploy |

## Warnings

| code | when |
| --- | --- |
| `offsite_backup.W001` | the store is a `DirectoryStore` — no immutability, on the machine it protects |
| `offsite_backup.W002` | encryption is the test `Passthrough` — nothing is encrypted |
| `offsite_backup.W003` | the once-per-night guard's `cache` backend uses a `LocMemCache` or `DummyCache` (not raised for the `database` backend) |
| `offsite_backup.W004` | a test run with `NOTIFY` set and notifiers not silenced |
| `offsite_backup.W005` | a media `ROOT`, an optional `CONFIG` path or the `RENDER_ENV` file does not exist here |

## Why `E009` is a deploy check

A required `CONFIG` path is a promise that the pass fails without it, so its absence is an
error, not a warning. But the plain settings check runs on **every** `manage.py` command, in
every container that loads the settings — the web service, a one-off `migrate`, a shell —
and most of them never run a pass and have no reason to mount the deployment directory. An
error there would stop `migrate` in a container that was never meant to back anything up.
So the plain check says nothing about a required path (and still warns `W005` for the
optional ones), and `check --deploy` — run where the deploy runs the pass — reports it as
`E009`. The pass itself fails on it either way.

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
