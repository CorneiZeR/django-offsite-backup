# Upgrading

## From 0.1 to 0.2

**Nothing breaks.** No setting is renamed or removed, and every new one defaults to what 0.1
did. Bump the pin and run `python manage.py check --deploy`.

Two things behave differently without a setting, both for the better:

* **Blank values are unset.** `None`, `''`, `0` and a list of only blanks no longer pass for
  configured. A machine that has the store credentials but read `['']` for `RECIPIENTS`, or
  `0` for a notifier's `CHAT_ID`, was silently half-configured; it now reports `E001` by name.
  Set the variable it names.
* **The morning check** no longer reports MISSING for a database with no backup at all
  beside databases that are backed up — a tenant created after the nightly pass. It is
  logged at INFO instead.

And the nightly task now **retries** (three times; Celery out of the box, taskiq once the
broker has `SimpleRetryMiddleware`). Each failed attempt sends its own FAILED message.
[Scheduling](Scheduling.md#retries) says how to turn it off.

### What to consider turning on

* **Your own test runner** — mix in `OffsiteBackupRunnerMixin`, so `--parallel` workers are
  silenced ([Testing](Testing.md#parallel-workers)). A project that subclassed
  `ParallelTestSuite` only to silence its workers can drop that code.
* **`'RECIPIENTS': os.environ.get('BACKUP_AGE_RECIPIENTS', '')`** in place of hand-written
  splitting ([Settings](Settings.md#blank-means-unset)).
* **`--reason deploy`** on the deploy line, if it is missing — the notice on stderr says so —
  or `DEFAULT_REASON` ([Deploy integration](Deploy-integration.md)).
* **`REQUIRED`** on `CONFIG` for the paths a restore cannot do without, or `STRICT` for the
  whole pass ([Settings](Settings.md#strict-and-required)); then `check --deploy` in the
  service the deploy runs the pass in, for `E009`.
* **`ADDRESSING_STYLE: 'path'`** for a MinIO or SeaweedFS store or media bucket
  ([S3 providers](S3-providers.md#addressing-style)).
* **`DELIVERY: 'queue'`** on `TopicNotifier`, to keep the bot token in the bot container
  ([Notifications](Notifications.md#direct-or-queued)).
* **`GUARD['BACKEND']: 'database'`**, if a cache was configured only for the guard
  ([Scheduling](Scheduling.md#once-per-night)).
* **A heartbeat** for `offsite_backup.nightly` and `offsite_backup.check`, if the project
  tracks its scheduled tasks ([Monitoring](Monitoring.md#heartbeats-of-scheduled-tasks)).

### Mixed database suffixes

A project switching to `FORMAT: 'custom'` from its own custom-format dumps named
`.sql.zst.age` sees both `.sql.zst.age` (old) and `.dump.zst.age` (new) names in a database's
folder for one retention window. Expected and harmless: rotation reads nights from the
stamps, and a restore detects the format from the magic bytes
([Databases](Databases.md#formats)).

## Versioning

The package follows [semantic versioning](https://semver.org), with one rule spelled out
because it is the one that bites in a backup tool:

* **A settings rename, a removed setting, or a default that flips is a major version.**
  A default decides what a machine backs up and how long it keeps it; changing it under a
  minor bump would change production in silence.
* **Object names are a compatibility surface.** A release that changes how keys are named
  keeps reading the old names — for rotation, the morning check and restore — until
  objects under them have rotated out, and says so here.
* **Check codes are stable.** A code never changes meaning; a new check gets a new code.
* Anything else that changes behaviour is a minor version, and is in the
  [Changelog](Changelog.md).

While the major version is 0, a minor release may still carry a breaking change, and if it
does this page says exactly what to change.

## How to upgrade

1. Read the [Changelog](Changelog.md) between your version and the new one, and this page.
2. Bump the pin; run `python manage.py check --deploy` in the new image.
3. Run `backup_offsite --reason manual` once after deploying and read the message.
4. Run the [restore drill](Restore.md#the-monthly-drill) if the release touched naming,
   encryption or restore.
