# Changelog

All notable changes to this project are documented here. The project follows
[semantic versioning](https://semver.org/): renaming a setting or flipping a default is a
major version.

## Unreleased (0.4.0)

No setting is renamed or removed, and no default flips. Two things a reader of the store
sees change on their own: every media volume now has a manifest beside it, and the pass's
object count includes them. Nothing to edit on upgrade.

### Sources and stores

Added

- `media.docker_volume.VolumeSource`: a named Docker volume the worker does not mount, read
  through a container that is created read-only with no network and never started, streamed
  once with `docker cp`; `VOLUME`, `IMAGE`. A missing volume is never created. `E011` under
  `check --deploy` when the daemon cannot be asked; `W005`/`E009` for a missing volume.
- `media.django_storage.StorageSource`: media through any Django storage (`STORAGE`, `PREFIX`),
  read a block at a time; a django-storages S3 file is streamed from its object's body rather
  than spooled whole. `E001` for an unknown alias, `E011` for a `PREFIX` that cannot be listed.
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
- `E011`; `Backend.binaries()` for a backend whose programs an option chooses.

Changed

- The pass's object count (message, `backup_offsite`, `ob_objects`) includes manifests, and
  counts each object once however many stores hold it.
- `fetch()` / `backup_restore --stamp` also brings back each volume's manifest.
- `pass_.Components` holds `stores` (a list); `Components.store` is the first of them, as
  before. `Wrote` carries the `store` it went to; `Summary` adds `objects`, `stores` and
  `failed_stores`, and its `bytes` and `keys` count each object once.
- `MediaSource.seal_volumes()` packs and seals while the files stream; `items()` is the one
  method a source that can only stream overrides. `volumes()` and `seal_volume()` remain.

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
