# Changelog

All notable changes to this project are documented here. The project follows
[semantic versioning](https://semver.org/): renaming a setting or flipping a default is a
major version.

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
