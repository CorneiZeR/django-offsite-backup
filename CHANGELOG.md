# Changelog

All notable changes to this project are documented here. The project follows
[semantic versioning](https://semver.org/): renaming a setting or flipping a default is a
major version.

## 0.5.0 — 2026-11-10

- **`backup_offsite --path`** (repeatable) puts a file or folder into this pass's configuration
  archive, as `offsite-backup run --path` already did on a plain server, and says each one that went
  up on a line of its own after the summary (`backup_offsite: up PATH`). For a deploy that hands over
  the dump it took before migrating and deletes it once it is off the box. `run(paths=...)` is the
  same from Python. Refused with `--only` naming other kinds than `config`.
- **A path handed over with `--path` must go up whole, on both commands** (`HANDED`, see
  Configuration archive). Missing, holding a file the process cannot read, or holding one `EXCLUDE`
  or the live `.env` rule would leave out, it fails the pass. Before, the command line's `--path` was left out with a
  warning when missing, and an unreadable file under it was skipped unless `STRICT` — a script
  deleting what it handed over would have deleted what never went up. `run --path` with `--only`
  leaving `config` out is refused (exit 2) instead of passing without the path, and each path that
  went up is said after the summary (`offsite-backup: up PATH`). `CONFIG`'s own `PATHS` keep their
  behaviour (`REQUIRED`, `STRICT`).

## 0.4.1 — 2026-11-04

- **The Telegram token and the webhook URL stay out of the HTTP client's log.** `httpx2` logs every
  request at INFO with its URL, and the Bot API carries the token in the path: wherever the logging
  shows INFO for `httpx2` (the command line does), the token was written to the journal. A filter on
  the client's logger now replaces both with `<redacted>`, whoever configured the logging. **Rotate a
  bot token that ran under 0.4.0's command line, or under a Django project logging `httpx2` at INFO.**

## 0.4.0 — 2026-11-04

No setting is renamed or removed; every new one is off until set. Three behaviours move:
the base install no longer pulls Django (the `[django]` extra does), a restore's emptiness
check and `E004` ask the server through `psql`, and a failed `--reason upgrade` pass says
the upgrade stops. And every media volume now has a manifest beside it, which the pass's
object count includes. Nothing to edit in a Django project.

### Operations

Added

- The restore drill: `manage.py backup_drill`, `offsite-backup drill`, `run_drill()`. The
  newest whole backup restored into the throwaway `DRILL['DATABASE']` (never a live one —
  the same alias, or the same host, port and name, is refused), every table counted there
  and live (`COMPARE_LIVE`), every media volume (`MEDIA`) and the configuration archive
  decrypted and listed, the outcome said (`drilled` / `drill_failed` messages), the target
  emptied. One database that does not come back does not stop the others; `DrillFailedError`
  names each with its step. Identity from `--identity`, `DRILL['IDENTITY_FILE']` or
  `AGE_IDENTITY`.
- `E011` (deploy): `DRILL['DATABASE']` is a live database or names none.
- Drill schedule entries, off by default: Celery `beat_schedule(drill=(day, hour, minute))`
  and the `offsite_backup.drill` task; taskiq `register(broker, drill='0 5 1 * *')`.
- `DatabaseSource.tables()` and `clear()`, what the drill asks of a source; `PostgresSource`
  implements both through `psql`.
- `WebhookNotifier` (`[webhook]`): a JSON POST of `Outcome.as_dict()` and the text; `SECRET`
  signs the body (`X-Offsite-Backup-Signature: sha256=…`); `HEADERS`, `TIMEOUT`, `RETRIES`
  on a network error, 429 or 5xx; `E010` for each option of the wrong form.
- `manage.py backup_status` / `offsite-backup status` / `freshness()`: each kind's newest
  whole backup and its age; exit 1 when stale, 2 when the store cannot be read;
  `--prometheus FILE` writes a textfile atomically; `--json`.
- The `offsite-backup` command (also `python -m offsite_backup`): `run` (with `--path`),
  `check`, `status`, `list`, `restore`, `drill`, `checks` (exit 3 when off), `failed`. A
  TOML setting with `${NAME}` / `${NAME:-default}` from the environment and `--env-file`
  files; `conf.configure()` for the same from Python.
- `PostgresSource` `CONNECTIONS`: connection blocks by alias, read instead of Django's
  `DATABASES`.
- Extras `django`, `cli`, `webhook`.

Changed

- Django is no longer a dependency of the base install: the core, every backend and the
  command line import without it; the app, the commands, the system checks' registration,
  the scheduling adapters and the test runner are the Django layer. Backends answer
  `check()` with `offsite_backup.findings`, which the Django layer hands to Django.
- A restore's "is the target empty" and `E004`'s server version are asked through `psql`
  rather than a Django connection.
- A failed pass with `--reason upgrade` says `The upgrade stops here; the running <NAME>
  keeps serving.`
- The test runner classes live in `offsite_backup.testing.runner`; `offsite_backup.testing`
  still exports them, imported when first named.

### Sources and stores

Added

- `media.docker_volume.VolumeSource`: a named Docker volume the worker does not mount, read
  through a container that is created read-only with no network and never started, streamed
  once with `docker cp`; `VOLUME`, `IMAGE`. A missing volume is never created. `E012` under
  `check --deploy` when the daemon cannot be asked; `W005`/`E009` for a missing volume.
- `media.django_storage.StorageSource`: media through any Django storage (`STORAGE`, `PREFIX`),
  read a block at a time; a django-storages S3 file is streamed from its object's body rather
  than spooled whole. `E001` for an unknown alias, `E012` for a `PREFIX` that cannot be listed.
- Media manifests: `<stamp>.<NNN>.manifest.json.age` beside every volume — each file's path,
  size and SHA-256, taken as it streams into the tar. Volumes are packed while files stream,
  so a source is listed once.
- Selective restore: `backup_restore --path` (repeatable; a file or a folder) downloads only
  the volumes whose manifest lists a match and checks every file against its hash;
  `restore_media()`; `backup_restore --database ALIAS` without `--apply` fetches one database.
- `databases.mysql.MySQLSource` (`CLIENT: 'mysql' | 'mariadb'`, `EXTRA_ARGS`):
  `--single-transaction`, the password in a 0600 option file, restore through the client.
- `databases.sqlite.SQLiteSource`: the online backup API, verified by `PRAGMA integrity_check`,
  restored with the same API.
- Several stores: `STORE` takes a list of blocks, each with a `NAME` (`E001` when missing or
  taken) and its own `KEEP_NIGHTS`. Every object goes to each store, locks read back on each,
  each rotated on its own; a store that refuses is left out of the pass, not rotated, and the
  pass fails by its name. The morning check and the database guard ask every store.
  `--store NAME` on `backup_list` and `backup_restore`; `store=` on `fetch`, `restore_media`,
  `restore_database`.
- `encryption.pyrage.Pyrage` (`[pyrage]` extra): age without the binary, the same format both
  ways; `PYTHON` for a server whose `sys.executable` is not Python.
- `E012`; `Backend.binaries()` for a backend whose programs an option chooses.

Changed

- The pass's object count (message, `backup_offsite`, `ob_objects`) includes manifests, and
  counts each object once however many stores hold it.
- `fetch()` / `backup_restore --stamp` also brings back each volume's manifest.
- `pass_.Components` holds `stores` (a list); `Components.store` is the first of them, as
  before. `Wrote` carries the `store` it went to; `Summary` adds `objects`, `stores` and
  `failed_stores`, and its `bytes` and `keys` count each object once.
- `MediaSource.seal_volumes()` packs and seals while the files stream; `items()` is the one
  method a source that can only stream overrides. `volumes()` and `seal_volume()` remain.
- In the drill and the command line: `MySQLSource` and `SQLiteSource` answer `tables()`,
  `clear()` and `same_database()` (SQLite by the file), and take `CONNECTIONS` like
  `PostgresSource`; `VolumeSource` and `Pyrage` run without Django; `StorageSource` is refused
  by name (`E002`) on a server with no Django project. The drill skips manifests, as what it
  opens is the volumes; `offsite-backup run|list|restore` take `--store`, and `restore` takes
  `--path` and narrows by `--database`, as the management commands do.

## 0.3.0 — 2026-11-04

No setting is added, renamed or removed. Two check results move; nothing to edit on upgrade
unless a pipeline relied on a plain `manage.py check` for W005 (use `check --deploy`).

Changed

- W005 (a media `ROOT`, an optional `CONFIG` path or the `RENDER_ENV` file not found) is a
  deploy check: a plain `manage.py check` or `migrate`, in a web or migrations container
  without the mount, no longer prints it; `check --deploy` does, beside E009. The pass
  itself logs each path it left out (`Configuration path not found; left out`,
  `Media directory not found`), so the worker, which runs no system check, still says it.
- A notifier option of the wrong form has its own code, E010 (was E001): a `TOPIC_ID` that is
  not an integer — one check shared by `TelegramNotifier` and `TopicNotifier` — and a
  `TopicNotifier` `DELIVERY` other than `direct`/`queue`.

## 0.2.0 — 2026-11-03

New settings default to 0.1 behaviour. Three behaviours change on their own: a blank
recipient or `0` chat is now E001 instead of passing; the nightly task retries; and the
morning check no longer calls a brand-new database MISSING. Nothing to edit on upgrade.

Fixed

- `OffsiteBackupTestRunner` now silences notifiers in every `--parallel` worker, whatever
  the multiprocessing start method (spawn on macOS, forkserver on Linux from Python 3.14):
  `SilencedParallelTestSuite` silences each worker, and the runner sets
  `OFFSITE_BACKUP_SILENCE=1`, which workers inherit and `notify` honours. The pytest plugin
  covers pytest-xdist workers.
- Blank values count as unset: `[os.environ.get('X', '')]`, `''` and `0` no longer pass as a
  configured recipient, chat or option, so E001 names them. Blank items drop from list options.

Added

- `OffsiteBackupRunnerMixin`, to mix the silencing into a project's own test runner.
- `TopicNotifier`: `DELIVERY: 'queue'` (through the bot container's queue, no token in the
  worker) and `BOT_FACTORY` (any bot wrapper). `TOPIC_ID` is validated on both Telegram
  notifiers; `0` means the main thread.
- S3: `ADDRESSING_STYLE` (`path` for MinIO and SeaweedFS), `SIGNATURE_VERSION`, `VERIFY` on
  `S3Store` and `S3Source`.
- `RECIPIENTS` accepts a separated string (`age1a, age1b`).
- `DEFAULT_REASON`; `backup_offsite --without-media` without `--reason` suggests `--reason deploy`.
- `STRICT` and per-source `REQUIRED`: a missing required path, an unreadable file (strict) or an
  empty required media source fails the pass, named in the message; E009 under `check --deploy`.
- `GUARD['BACKEND'] = 'database'`: the once-per-night guard on a PostgreSQL advisory lock and the
  store itself, with no cache.
- Retries: the nightly task retries 3 times on both Celery and taskiq (`register(..., retries=)`);
  a retry re-enters the guard. The morning check is not retried.
- The morning check no longer reports a database with no backup at all, beside backed-up ones,
  as MISSING (a tenant created after the nightly pass); stale ones still are.
- `restore_database(identity_text=...)`.

## 0.1.0 — 2026-11-03

First release.

- A pass: PostgreSQL databases (a list, or computed at run time; plain or custom format),
  media in volumes of whole files (a directory, a Docker volume mounted as one, or an S3
  bucket), and the deployment configuration with the environment rebuilt from a list of
  names — compressed with zstd, encrypted with age to one or more recipients.
- Stores: S3 with Object Lock COMPLIANCE passed on every upload and read back, versions and
  delete markers, pagination; a local directory (no immutability, W001).
- Whole-night rotation per kind; passes without media (`--without-media`) never count as a
  night; depth reported after rotation.
- One database failing does not cost the others their night; the pass fails at the end
  naming each one.
- Once-per-night guard for the nightly pass; the morning check (MISSING / UNCHECKED).
- Messages in one HTML format; notifiers: log, Telegram (`[telegram]`), django-aiogram
  (`[aiogram]`). Notifiers are silenced for every test run.
- `back_up_database` (extra recipients, `keep_apart`), `keep_apart`, `restore_database`.
- Commands: `backup_offsite`, `backup_check`, `backup_list`, `backup_restore` (download,
  decrypt, verify; `--apply` loads a database).
- System checks E001–E008, W001–W005.
- Celery and taskiq adapters.
- `offsite_backup.testing`: FakeS3, Passthrough, `use_fake_s3`, `capture`, the test runner
  and the pytest plugin.
