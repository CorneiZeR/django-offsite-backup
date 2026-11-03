# Changelog

## 0.1.0 — unreleased

The first release.

**A pass.** `manage.py backup_offsite` (and `offsite_backup.run()`) backs up the
databases, the media and the deployment configuration, one part at a time, then rotates.
A database that fails does not cost the others their night; the pass fails at the end
naming every one. `--without-media` and `--only` for partial passes; `--reason` for the
message.

**Databases.** `offsite_backup.databases.postgres.PostgresSource`: one alias, a list, or a
callable that computes them at run time; plain SQL (`.sql.zst.age`) or the custom format
(`.dump.zst.age`, `pg_dump`'s compression off, then zstd). The password travels in
`PGPASSWORD`, never on a command line. `back_up_database()` for a one-off dump with extra
recipients, and `keep_apart` to set one aside from rotation.

**Media.** `offsite_backup.media.filesystem.DirectorySource` and
`offsite_backup.media.s3.S3Source`: volumes of whole files (4 GiB by default), each a
complete tar written with Python's `tarfile`, streamed one at a time; `INCLUDE`/`EXCLUDE`
globs; several sources.

**Configuration.** `offsite_backup.config.paths.PathsSource`: listed paths plus the
environment rebuilt from a list of names, written last.

**Encryption.** `offsite_backup.encryption.age_cli.AgeCli`: compress, then encrypt, to one
or more public keys, under pipefail semantics.

**Stores.** `offsite_backup.stores.s3.S3Store`: every upload locked COMPLIANCE by its own
arguments and read back; multipart in 64 MiB parts; versions and delete markers handled;
pagination everywhere; only the configured credentials.
`offsite_backup.stores.filesystem.DirectoryStore`: atomic writes, `0600` files, and a
warning that it has no immutability.

**Retention.** Whole UTC nights read from object names, per kind; passes without media never
count as nights; every version deleted; locked versions left for a later night; depth in
the message.

**Messages.** One HTML format for written, failed, missing and unchecked, every value
escaped. Notifiers: `LogNotifier`, `TelegramNotifier` (`[telegram]`, httpx2),
`TopicNotifier` through django-aiogram (`[aiogram]`). A notifier never costs the backup.

**Scheduling.** `run_nightly()` with a once-per-night guard in a shared cache; Celery
(`offsite_backup.schedule.celery.beat_schedule`) and taskiq
(`offsite_backup.schedule.taskiq.register`) adapters.

**Monitoring.** `manage.py backup_check` — the morning check over the configuration folder
and every database's; `LAST_SUCCESS_FILE`; `manage.py backup_list`.

**Restore.** `manage.py backup_restore`: download, decrypt and verify every object of a
stamp; `--apply` loads a database (`--from-database`, `--clean`); `restore_database()`.

**System checks.** `E001`–`E008`, `W001`–`W005`; network checks under `check --deploy`
only.

**Testing.** A pytest plugin and `OffsiteBackupTestRunner` that silence network
notifiers; `capture()`, `FakeS3`, `use_fake_s3`, `Passthrough`, `media_tree`.
