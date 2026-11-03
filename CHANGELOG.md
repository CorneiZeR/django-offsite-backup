# Changelog

All notable changes to this project are documented here. The project follows
[semantic versioning](https://semver.org/): renaming a setting or flipping a default is a
major version.

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
