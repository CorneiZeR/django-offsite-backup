# AI assistants

Coding agents wire this package into a project the way a person would — by reading the
docs — and the mistakes they make are the expensive kind: a private key on the server, a
test suite posting to the team's chat, a bucket without Object Lock. This page is the
brief to hand them.

## Paste this in

Short enough for a system prompt or a `CLAUDE.md` / `AGENTS.md` / `.cursor/rules` file:

```text
Project uses django-offsite-backup 0.4. Rules:

- Install with the extras the configuration names, nothing inferred:
  `django-offsite-backup[s3,telegram]` for an S3 store and Telegram messages,
  `[s3,aiogram]` to send through the project's django-aiogram bot. Add
  'offsite_backup' to INSTALLED_APPS. It has no models and no migrations.
- The image that runs a pass needs the binaries: pg_dump (major >= the
  PostgreSQL server's), zstd, age. Not tar. Install them in the worker's image
  AND the image the deploy pass runs in.
- Settings are one dict, OFFSITE_BACKUP, in settings.py. Read EVERY credential
  from the environment with a default of '' — os.environ.get('X', '') — never
  os.environ['X'] and never a placeholder value. Empty means "backups off on this
  machine", which is what developer laptops and CI want.
- The age RECIPIENTS is a public key and may be read from the environment or
  written into settings; it does not switch backups on by itself. Read it with
  the same idiom, as a plain string:
  'RECIPIENTS': os.environ.get('BACKUP_AGE_RECIPIENTS', '') — a string splits
  on commas (and between age1 keys on whitespace). Do not wrap it in a list or
  split it yourself. Blank values (None, '', 0, [''] ) count as unset, so E001
  still names a missing variable.
- Backend paths, exactly:
  offsite_backup.stores.s3.S3Store / offsite_backup.stores.filesystem.DirectoryStore
  offsite_backup.encryption.age_cli.AgeCli
  offsite_backup.databases.postgres.PostgresSource
  offsite_backup.media.filesystem.DirectorySource / offsite_backup.media.s3.S3Source
  offsite_backup.config.paths.PathsSource
  offsite_backup.notifiers.telegram.TelegramNotifier
  offsite_backup.notifiers.django_aiogram.TopicNotifier
  offsite_backup.notifiers.log.LogNotifier
  offsite_backup.notifiers.webhook.WebhookNotifier   ([webhook] extra)
- NEVER put an age private key (AGE-SECRET-KEY-...) on the server, in settings,
  in .env, in CI variables, or in the repository. RECIPIENTS takes public keys
  (age1...). The private key is generated on a person's machine with age-keygen
  and kept in their password manager. E008 refuses a private key in RECIPIENTS.
  Do not generate a key pair for the user: tell them to run age-keygen and give
  you the public half.
- The S3 bucket must be created WITH Object Lock (it cannot be enabled later)
  and given a default retention rule COMPLIANCE, days == STORE['LOCK_DAYS']
  (13). Keep RETENTION['KEEP_NIGHTS'] (14) greater than LOCK_DAYS. Never
  suggest GOVERNANCE mode. Never "fix" E005/E006 by setting LOCK_DAYS to 0
  without the user explicitly accepting backups that the server can delete.
- PREFIX is required ('<project>/<env>', e.g. 'acme/prod'). Rotation never
  leaves it.
- Scheduling: Celery -> CELERY_BEAT_SCHEDULE = {**beat_schedule(), ...} from
  offsite_backup.schedule.celery, with CELERY_IMPORTS including
  'offsite_backup.schedule.celery' and the beat in UTC. taskiq ->
  register(broker) from offsite_backup.schedule.taskiq. cron -> 03:00 UTC
  `manage.py backup_offsite --reason nightly`, 09:00 UTC `manage.py backup_check`.
  The guard needs GUARD['CACHE'] to be a cache shared by every worker (Redis,
  Memcached, database) — not LocMemCache (W003) — or, in a project without a
  shared cache, GUARD = {'BACKEND': 'database'} (a PostgreSQL advisory lock).
  Celery's beat entries are in CELERY_TIMEZONE; do not switch a project's beat
  to UTC without asking (it moves every other entry) — pass local hours to
  beat_schedule() instead. taskiq retries need SimpleRetryMiddleware on the
  broker.
- Deploy: ALWAYS write the full line
  `python manage.py backup_offsite --without-media --reason deploy` — both
  flags; without --reason the pass reads "By hand" and its failure does not
  say the deploy stops. Run it on the NEW image BEFORE `migrate`, and let a
  non-zero exit stop the deploy. Do not add `|| true`. Mount the deployment
  directory read-only into the service that runs it AND into the worker.
- Validate with `python manage.py check` and `python manage.py check --deploy`.
  Missing mounts (W005, E009 for a REQUIRED path) are reported by
  `check --deploy` only — run it in each service that runs a pass (the deploy's
  and the worker); a plain check is quiet about paths on purpose. Treat offsite_backup.E0xx/W0xx as the spec. Do not silence an error; only
  W001 (directory store) may be silenced, and only where the user chose it.
- Tests must never notify. pytest is covered by the package's plugin
  automatically, pytest-xdist workers included; with Django's runner set
  TEST_RUNNER = 'offsite_backup.testing.OffsiteBackupTestRunner'. If the
  project ALREADY has its own runner, do not replace it: mix the behaviour in,
  mixin first — class Runner(OffsiteBackupRunnerMixin, ExistingRunner). This
  also covers `manage.py test --parallel` workers (spawn/forkserver). If that
  runner sets its own parallel_test_suite, make it a subclass of
  offsite_backup.testing.SilencedParallelTestSuite. Do not give CI test jobs
  the production notifier token or store keys. In
  tests use offsite_backup.testing: use_fake_s3(FakeS3()), Passthrough as the
  ENCRYPTION backend with COMPRESS [], capture() to assert on messages,
  media_tree() for media. Passthrough is for tests only (W002).
- Restore: `manage.py backup_restore --stamp latest --into DIR` with the
  private key in AGE_IDENTITY or --identity FILE, run by a person on their own
  machine. Never write code that stores the identity on the server.
- The restore drill (`manage.py backup_drill`) restores into
  DRILL['DATABASE'], which must be a throwaway database — never an alias, host+
  port+name of a live one (refused; E011). It needs the private key: do not
  schedule it on the production worker unless the user explicitly accepts the
  key there; prefer a separate machine running `offsite-backup drill`. Its
  schedule is off by default — beat_schedule(drill=(1, 5, 0)) /
  register(broker, drill='0 5 1 * *') only when asked.
- Monitoring: `manage.py backup_status` exits 1 when a kind is stale, 2 when
  the store is unreadable; `--prometheus FILE` for a textfile collector.
- A server with no Django project uses the `offsite-backup` command with a TOML
  setting (same keys as OFFSITE_BACKUP, secrets as ${VAR} from the environment
  or --env-file, never literal), PostgresSource CONNECTIONS for its databases,
  and cron with flock. Install `django-offsite-backup[cli,s3,...]` with pipx.
```

## Prompts that work

**Wire it in.** *"Add django-offsite-backup to this project: S3 store with credentials
from `BACKUP_S3_*` variables, age recipient from `BACKUP_AGE_RECIPIENT`, PostgreSQL
`default`, media from `MEDIA_ROOT`, the `deploy/` directory as configuration, Telegram from
`BACKUP_TELEGRAM_*`. Add the Celery beat entries, the pre-migration step to our deploy script,
the read-only mount on web and worker, the binaries to the Dockerfile, and
`OffsiteBackupTestRunner`. Do not generate keys. Show me `manage.py check` output with the
variables unset."* — the last sentence is the test that the laptop stays "off".

**Review a wiring.** *"Check our OFFSITE_BACKUP settings and deploy script against the
django-offsite-backup docs: credentials from the environment with '' defaults, no private
key anywhere, backup before migrate with a failing exit stopping the deploy, mounts on
both services, notifier silenced in tests."*

**Prepare a bucket.** *"Give me the AWS CLI commands to create a bucket with Object Lock and
a default COMPLIANCE rule of 13 days on <provider>, and a least-privilege policy for the
backup key under prefix `acme/prod/`. Do not run them."*

## What to check after an agent is done

* `grep -rn "AGE-SECRET-KEY" .` finds nothing — in the repository, the CI configuration or
  the server's `.env`.
* `python manage.py check` with the backup variables **unset** reports no `offsite_backup`
  code (the machine is off); with them set, none but warnings you chose.
* `python manage.py check --deploy` on the server: no `E004`, `E005`, `E006`.
* The deploy script stops when `backup_offsite` exits non-zero — try it once with a wrong
  bucket name on staging — and its line carries `--reason deploy`.
* The test job's environment holds no production notifier token, and a project with its
  own test runner mixes in `OffsiteBackupRunnerMixin` rather than losing it.
* Someone has restored a backup — [Restore](Restore.md).
