# Changelog

## 0.4.1 — 2026-11-04

- **The Telegram token and the webhook URL stay out of the HTTP client's log.** `httpx2` logs every
  request at INFO with its URL, and the Bot API carries the token in the path: wherever the logging
  shows INFO for `httpx2` (the command line does), the token was written to the journal. A filter on
  the client's logger now replaces both with `<redacted>`, whoever configured the logging. **Rotate a
  bot token that ran under 0.4.0's command line, or under a Django project logging `httpx2` at INFO.**

## 0.4.0 — 2026-11-04

Nothing to edit in a Django project; [Upgrading](Upgrading.md#from-03-to-04) has the
behaviours that move and what to consider turning on.

### Operations

**The restore drill.** `manage.py backup_drill` (and `offsite-backup drill`, `run_drill()`)
downloads the newest whole backup, decrypts it, restores every database into the throwaway
`DRILL['DATABASE']` — never a live one: a target reaching a database the pass dumps is
refused, and is `E011` under `check --deploy` — counts every table there and live, opens
every media volume and the configuration archive, says the outcome through the notifiers
and empties the target. A schedule entry for it in both adapters, off unless asked
(`beat_schedule(drill=...)`, `register(broker, drill=...)`). [Restore](Restore.md#the-monthly-drill).

**A webhook notifier.** `WebhookNotifier` (`[webhook]`) POSTs the outcome as JSON beside its
text, signed with HMAC-SHA256 when given a `SECRET`, retried on a network error, a 429 or a
5xx; `E010` checks its options. [Notifications](Notifications.md#a-webhook).

**The age of every kind, for a monitor.** `manage.py backup_status` (`offsite-backup status`,
`freshness()`) answers each kind's newest whole backup and its age, exits 1 when one is
stale and 2 when the store cannot be read, and writes a Prometheus textfile with
`--prometheus`. [Monitoring](Monitoring.md#the-age-of-every-kind).

**Without Django.** The `offsite-backup` command runs the same passes, checks, restore and
drill on a server with no Django project, from a TOML file whose top level is
`OFFSITE_BACKUP`, with `${NAME}` read from the environment and `--env-file` files.
`PostgresSource` takes its connections from `CONNECTIONS`. Django is the `[django]` extra;
the core imports without it. [Without Django](Plain-servers.md).

### Sources and stores

**More sources.** MySQL and MariaDB (`mysqldump --single-transaction`, the password in a
private option file) and SQLite (the online backup API) beside PostgreSQL
([Databases](Databases.md)); a Docker volume the worker does not mount, read through a
container that never starts, and any Django storage, streamed a block at a time
([Media](Media.md)).

**Manifests and selective restore.** Every media volume has a sealed manifest beside it —
path, size and SHA-256 of each file. `backup_restore --path` brings back one file or one
folder, downloading only the volumes that hold it and checking every file against its hash;
`--database ALIAS` brings back one database ([Restore](Restore.md#some-media-files)).

**Several stores.** `STORE` takes a list: every pass goes to each store, each read back,
locked and rotated on its own; a store that refuses fails the pass by its name, never a
silent partial ([Several stores](Stores-several.md)).

**pyrage.** `encryption.pyrage.Pyrage` seals in age's format without the binary; either
opens what the other sealed ([Encryption](Encryption.md#without-the-binary-pyrage)).

Every new source runs under the drill and the command line too — MySQL, MariaDB and SQLite
are counted and emptied by the drill like PostgreSQL; a Django storage needs a Django project
and is refused by name without one ([Without Django](Plain-servers.md)).

## 0.3.0 — 2026-11-04

Nothing to edit on upgrade; [Upgrading](Upgrading.md#from-02-to-03) has the two checks that
moved.

**Path checks only where a pass runs.** `W005` — a media `ROOT`, an optional `CONFIG` path or
the `RENDER_ENV` file not found — was printed by every container that ran `manage.py` on a
deploy (the web service, a `migrate`), though none of them runs a pass or mounts the
deployment directory. It is now a deploy check, beside `E009`: `check --deploy`, run where
the pass runs, reports it, and a plain `check` or `migrate` does not. The pass logs each path
it leaves out (`Configuration path not found; left out`, `Media directory not found`), so the
worker says it too. [System checks](System-checks.md#why-the-path-checks-are-deploy-checks).

**`E010` for notifier options.** A `TOPIC_ID` that is not an integer — one check shared by
both Telegram notifiers — and a `DELIVERY` other than `direct`/`queue` are `E010` instead of
`E001`. [System checks](System-checks.md#errors).

## 0.2.0 — 2026-11-03

Nothing breaks: every new setting defaults to 0.1's behaviour. What to consider turning on
is on [Upgrading](Upgrading.md#from-01-to-02).

**Fixed: tests notified from parallel workers.** Under `manage.py test --parallel`, workers
started with spawn (macOS) or forkserver (Linux, Python 3.14) did not inherit the runner's
silence, and a configured notifier could post. The runner now sets
`OFFSITE_BACKUP_SILENCE=1` for the run, which `notify.silenced()` honours in every process,
and its `parallel_test_suite` is `SilencedParallelTestSuite`, whose `process_setup` silences
each worker. `OffsiteBackupRunnerMixin` brings both to a project's own runner. The pytest
plugin covers pytest-xdist workers, each of which loads it. [Testing](Testing.md#parallel-workers).

**Blank values are unset.** `None`, blank strings, `0` and lists made only of those count as
unset for required options, so `E001` names them — `[os.environ.get('X', '')]` and
`int(os.environ.get('X') or 0)` no longer pass for configured. Blank items are dropped from
`PATHS`, `INCLUDE`/`EXCLUDE` and `ALIASES` lists. `RECIPIENTS` takes a string or a list; a
string splits on commas, newlines and, between `age1…` keys, whitespace.
[Settings](Settings.md#blank-means-unset).

**`TopicNotifier`: queued delivery and a bot factory.** `DELIVERY: 'queue'` sends through
`bot.send` — the queue a bot container consumes, with no token in the worker; `'direct'`
(default) is `bot.send_raw`, as before. `BOT_FACTORY` names a callable returning any object
with `send_raw`/`send`. Both Telegram notifiers share `ChatNotifier`: `TOPIC_ID` is
validated on both, and `None`, blank or `0` means the main thread.
[Notifications](Notifications.md#direct-or-queued).

**S3 client options.** `ADDRESSING_STYLE` (`auto`, `path`, `virtual`), `SIGNATURE_VERSION`
and `VERIFY` (a bool, or a CA bundle's path) on `S3Store` and `S3Source`.
[S3 providers](S3-providers.md#addressing-style).

**`DEFAULT_REASON`.** The reason `backup_offsite` uses without `--reason` (default
`manual`). `--without-media` without an explicit `--reason` prints a one-line notice
suggesting `--reason deploy`. [Deploy integration](Deploy-integration.md).

**`STRICT` and `REQUIRED`.** `REQUIRED` on `CONFIG` (`True`, or a list of paths) and on a
media source; `STRICT` for the whole pass. A required path missing, an empty required media
source, or — under `STRICT` — an unreadable file fails the pass with `SourceMissingError`,
named in the FAILED message. New check `offsite_backup.E009` under `check --deploy`: a
required `CONFIG` path missing where it runs. [Settings](Settings.md#strict-and-required).

**The guard without a cache.** `GUARD['BACKEND']: 'database'` claims the night with a
PostgreSQL session advisory lock on `GUARD['DATABASE']`, and reads "done" from the store.
`'cache'` stays the default. [Scheduling](Scheduling.md#once-per-night).

**Retries.** The nightly task retries: Celery `autoretry_for=(Exception,)`, `max_retries=3`,
exponential backoff from a minute; taskiq `register(broker, retries=3)` puts
`retry_on_error`/`max_retries` labels on it, for `SimpleRetryMiddleware`. A retry re-enters
through the guard. The morning check is not retried. [Scheduling](Scheduling.md#retries-and-the-guard).

**The morning check and new databases.** A database with no backup at all, beside databases
that are backed up, is logged as "not backed up yet" instead of reported MISSING — a tenant
created after the nightly pass. Stale backups, and the only database with none, are still
MISSING. [Monitoring](Monitoring.md#a-database-added-today).

**Restore.** `restore_database(..., identity_text=...)` takes a private key's text, for a
dedicated identity the server holds on purpose; written to a private `0600` file and removed
afterwards. Passing a file and text together is refused. [Restore](Restore.md#3-load-a-database).

**Documentation.** A machine with only the recipient set is off; Celery beat entries follow
`CELERY_TIMEZONE`; the deployment mount on the deploy's service and the worker; restoring a
custom-format dump under a legacy `.sql…` name; mixed suffixes after switching formats;
heartbeats of the two scheduled tasks; new rules on [AI assistants](AI-assistants.md).

## 0.1.0 — 2026-11-03

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
