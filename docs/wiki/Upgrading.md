# Upgrading

## From 0.3 to 0.4

**Nothing to edit in a Django project.** No setting is renamed or removed, and every new one
is off until set. Bump the pin and run `python manage.py check --deploy`.

* **Django is an extra now.** The base install no longer pulls Django — a Django project
  has it pinned already, so nothing changes there. An environment that installed the
  package *alone* and relied on it to bring Django installs `django-offsite-backup[django]`.
* **A restore's emptiness check and `E004` ask the server through `psql`** instead of a
  Django connection — the same answers, on the same connection settings. `psql` was already
  required for a restore (`E003`).
* **`--reason upgrade`** now says on failure that the upgrade stops and the running service
  (`NAME`) keeps serving, as `--reason deploy` says for a deploy.
* **Every media volume has a manifest** beside it, `<stamp>.<NNN>.manifest.json.age`, sealed
  like the volume ([Media](Media.md#manifests)). It rotates with its volume. The pass's object
  count — the message's `📦 Objects`, `backup_offsite`'s line, the log's `ob_objects`, and
  `backup_list` — includes them; a dashboard alerting on an exact count moves by one per
  volume. `backup_restore --stamp` brings the manifests back beside the volumes.
* **Volumes are packed while files stream**: a source is listed once, interleaved with the
  uploads, instead of being listed whole before the first volume. Same cuts, same names.
* Volumes written by 0.3 have no manifest; `backup_restore --path` reads those whole.

### What to consider turning on

* **The monthly drill** — `DRILL['DATABASE']` and a schedule, after reading
  [where it runs](Restore.md#where-it-runs-the-identity).
* **`backup_status`** in your monitoring, or its Prometheus textfile
  ([Monitoring](Monitoring.md#the-age-of-every-kind)); `offsite_backup.drill` in a
  heartbeat table if you schedule the drill.
* **A webhook** beside the chat ([Notifications](Notifications.md#a-webhook)).
* **The command line** for a server with no Django project, in place of its own backup
  script ([Without Django](Plain-servers.md#coming-from-a-hand-written-script)).
* **A local copy beside S3** — `STORE` as a list, each with a `NAME` and its own
  `KEEP_NIGHTS` ([Several stores](Stores-several.md)).
* **One file back** — `backup_restore --path` ([Restore](Restore.md#some-media-files)).
* **A worker without the media mount** — `VolumeSource`, or `StorageSource` for media in a
  Django storage ([Media](Media.md)).
* **An image without `age`** — `Pyrage`; nothing already stored needs re-sealing
  ([Encryption](Encryption.md#without-the-binary-pyrage)).
* **MySQL, MariaDB or SQLite** — [Databases](Databases.md).

## From 0.2 to 0.3

**Nothing breaks.** No setting is added, renamed or removed. Bump the pin.

Two check results move:

* **`W005` is reported by `check --deploy` only.** A plain `manage.py check` or `migrate` in a
  container without the deployment or media mount is quiet now; the pass logs what it left
  out. `'offsite_backup.W005'` in `SILENCED_SYSTEM_CHECKS`, added to quiet the web service,
  can go. A pipeline that ran a plain `check --fail-level WARNING` to catch a missing mount
  runs `check --deploy` in the service that runs the pass instead
  ([System checks](System-checks.md#why-the-path-checks-are-deploy-checks)).
* **`E010`** replaces `E001` for a `TOPIC_ID` that is not an integer and an unknown
  `DELIVERY`. Only a configuration that silenced or matched on `E001` for those notices.

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
4. Run the [restore drill](Restore.md#the-monthly-drill) — `backup_drill` — if the release
   touched naming, encryption or restore.
