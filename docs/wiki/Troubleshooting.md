# Troubleshooting

Every system check code first, then what goes wrong at run time, then the S3 behaviours
this package was built around.

## Check codes

### `E001` — configured by halves

```text
?: (offsite_backup.E001) Backups are configured by halves; these are unset: OFFSITE_BACKUP['STORE']['SECRET_KEY'], OFFSITE_BACKUP['ENCRYPTION']['BACKEND']
	HINT: Set every one, or leave the store credentials empty to turn backups off on this machine.
```

**Cause.** Something switched backups on — a store credential or a `ROOT`
([the rule](Settings.md#enabled-off-on-or-refused)) — and something they need is empty.
Usually one environment variable missing from one container.

**Fix.** Set every setting the message names. If this machine should not back up at all
(a laptop, a CI job), make *all* the store options empty, or set `'ENABLED': False`.

The same code is used for values that are present but wrong, each with its own message:

| message | fix |
| --- | --- |
| `... (a positive integer, got ...)` for `KEEP_NIGHTS` | an `int` ≥ 1 |
| `['FORMAT'] is ...; expected one of plain, custom` | `'plain'` or `'custom'` |
| `['VOLUME_BYTES'] is ...; expected more than 20480 bytes and at most 5 GB` | the default 4 GiB is right for almost everyone |
| `['TOPIC_ID'] is ..., not an integer` | the numeric topic id, or empty |
| `GUARD['CACHE'] is ..., which CACHES does not define` | name a cache alias that exists |
| `... (nothing to back up)` | set at least one of `DATABASES`, `MEDIA`, `CONFIG` |

### `E002` — a backend that cannot be loaded

```text
?: (offsite_backup.E002) OFFSITE_BACKUP['STORE']['BACKEND'] is S3Store, but its driver is not installed.
	HINT: pip install "django-offsite-backup[s3]"
```

**Cause.** The extra is not installed in this image, the dotted path has a typo, or it
names a class that is not a subclass of the family's base (a `Store` where a `Sealer` is
expected, for example).

**Fix.** Install the extra the hint names; otherwise copy the path from
[Settings](Settings.md) — `offsite_backup.stores.s3.S3Store`, not `offsite_backup.stores.S3Store`.

### `E003` — a binary is missing

```text
?: (offsite_backup.E003) OFFSITE_BACKUP['DATABASES'] runs `pg_dump`, which is not on PATH.
	HINT: Install pg_dump in the image that runs the backup.
```

**Cause.** The image that runs the pass lacks `pg_dump`, `age` or `zstd`. Slim base
images do.

**Fix.** Install it in *that* image — the worker's, and the one the deploy pass runs in
([Installation](Installation.md#binaries)).

### `E004` — `pg_dump` older than the server

```text
?: (offsite_backup.E004) pg_dump is 16, older than the server of 'default' (18).
	HINT: Install the PostgreSQL 18 client (e.g. postgresql-client-18) in the image.
```

**Cause.** The database was upgraded and the application image was not. `pg_dump` refuses
to dump a newer server; every pass would fail.

**Fix.** Install the client of the server's major (or newer) from the PostgreSQL apt
repository. Deploy checks only (`check --deploy`).

### `E005` — the bucket cannot hold a locked backup

```text
?: (offsite_backup.E005) OFFSITE_BACKUP['STORE']: bucket 'acme-backups' has no Object Lock.
	HINT: Object Lock can only be enabled when a bucket is created: create a new bucket with it, or set LOCK_DAYS to 0 to store without immutability.
```

Four messages share the code:

| message | cause | fix |
| --- | --- | --- |
| `bucket ... could not be read (<code>)` | wrong `ENDPOINT_URL`, bucket name, key, or a network block | check each; the S3 error code is in the brackets |
| `bucket ... is not versioned` | created without Object Lock | a new bucket, created with Object Lock |
| `the Object Lock configuration of ... could not be read` | the key may not read it (`GetBucketObjectLockConfiguration`) | grant it ([S3 providers](S3-providers.md#aws-s3)) |
| `bucket ... has no Object Lock` | created without it | **a new bucket**, created with Object Lock; it cannot be switched on later on most providers. Move `PREFIX` there and let the old bucket's objects expire |

### `E006` — no default rule, or the wrong one

```text
?: (offsite_backup.E006) OFFSITE_BACKUP['STORE']: bucket 'acme-backups' has default retention none, not COMPLIANCE 13 days.
	HINT: Multipart uploads (anything over 64 MiB) take their lock date from this rule, not from the request; set a default rule of COMPLIANCE, 13 days.
```

**Cause.** The bucket has Object Lock but no default retention rule, a GOVERNANCE one, or
one of a different number of days than `LOCK_DAYS`. Every object uploaded in parts — every
media volume, any large database — would land with the bucket's date (or none) and fail
the read-back.

**Fix.** `put-object-lock-configuration` with `COMPLIANCE` and `Days` equal to `LOCK_DAYS`
([S3 providers](S3-providers.md)).

### `E007` — retention not longer than the lock

**Cause.** `KEEP_NIGHTS <= LOCK_DAYS`. Rotation would meet locked objects every night and
leave them, and the folder would hold more than you think.

**Fix.** Keep at least one night more than the lock: 14 nights, 13 days.

### `E008` — not a public key in `RECIPIENTS`

**Cause.** `RECIPIENTS` holds an `AGE-SECRET-KEY-…` (the private half), or a string that is
none of `age1…`, `ssh-ed25519 …`, `ssh-rsa …` — often several keys in one comma-separated
string, or a quoted value with the quotes kept.

**Fix.** The public half only (`age-keygen -y key.txt` prints it); a list for several. If a
private key was there, it has been on the server: make a new pair, and treat every backup
sealed to the old one as readable by whoever had the server.

### `W001` — a directory store

No immutability, on the machine it protects. Fine for a fast local copy or a staging
machine; silence it there (`SILENCED_SYSTEM_CHECKS`). For production, the disaster copy
belongs in [S3 with Object Lock](Stores-S3.md).

### `W002` — `Passthrough` encryption

The test sealer is configured: **nothing is encrypted**. It belongs in test settings only.
Use `offsite_backup.encryption.age_cli.AgeCli`.

### `W003` — the guard's cache is not shared

`GUARD['CACHE']` is a `LocMemCache` or `DummyCache`. Each worker has its own, so two
schedulers can both run tonight. Point it at Redis, Memcached or the database cache, or
turn the guard off if exactly one scheduler can ever run (`'GUARD': {'ENABLED': False}`).

### `W004` — notifiers in a test run

A test run (pytest, or `manage.py test`) with `NOTIFY` set and not silenced — with
pytest the plugin silences it, so this means Django's runner without
`OffsiteBackupTestRunner`. Set `TEST_RUNNER` ([Testing](Testing.md)), and keep production
notifier tokens out of the CI test job.

### `W005` — a path is missing here

```text
?: (offsite_backup.W005) OFFSITE_BACKUP['CONFIG']: /app/deploy not found here.
	HINT: Mount the deployment directory (read-only) into every container that runs the backup.
```

A media `ROOT`, a `CONFIG` path or the `RENDER_ENV` file is not there in *this* container.
Usually a volume mounted into the web service and not the worker, or the other way round.

## At run time

### `backup_offsite: not configured on this machine, nothing to do` — on production

None of the switching options reached this process: the variables are in one service's
`env_file` and not the one that ran the command, or a `.env` was not loaded. `manage.py
shell -c "from offsite_backup import conf; print(conf.read().values['STORE'])"` shows what
the process sees.

### `BackupUnlockedError: ... landed GOVERNANCE until ...` / `landed unlocked`

The object did not land with the lock asked. A short date on a large object means the
bucket's default rule is missing or different (`E006`); `unlocked` means the bucket has no
Object Lock (`E005`). The pass stops before rotation, so nothing older was deleted. Fix the
bucket and run `check --deploy`.

### `PipelineError` from `pg_dump`

The tail of `pg_dump`'s stderr follows. Common ones: `server version mismatch` (`E004`),
`password authentication failed` (the alias's `PASSWORD`), `permission denied for table`
(the role cannot read every table — dump with the owner role, or grant `SELECT`). The other
databases, the media and the configuration still went up.

### `PipelineError` from `age`

Usually a malformed recipient that slipped past `E008` (several keys in one string), or —
in a restore — the wrong identity: `no identity matched any of the recipients`.

### A database restore says "is not empty"

`backup_restore --apply` refuses a database that has tables. Restore into an empty one, or
add `--clean` to drop and recreate the `public` schema first.

### The morning check reports a database missing that was just added

With `ALIASES` computed at run time, a new database has no backup until its first night.
Run a whole pass (`backup_offsite`), or `back_up_database('<alias>')` from a shell. Not
`--only db` in a project with media: that pass is marked `.without-media.` and does not
count.

### Telegram: no message, `Telegram refused the backup message`

The description in the log says why: `message thread not found` is a wrong `TOPIC_ID`;
`chat not found` a wrong `CHAT_ID` or a bot that is not a member; `can't parse entities`
would mean a custom formatter that did not escape its values.

### The nightly task ran twice

The guard's cache is not shared (`W003`), or `GUARD['ENABLED']` is off. With a shared
cache the second run logs `Nightly backup skipped: tonight is being written`.

### The deploy waits a long time for the backup

The deploy pass includes media: add `--without-media`. If the database itself is the long
part, a lighter `COMPRESS` (`['zstd', '-10', '-T0', '-q']`) trades size for time.

## S3 behaviours this package is built around

Measured on a real S3 provider, each at the cost of a failed night; each is answered in the
code and pinned by a test.

| behaviour | what goes wrong if you rely on the other thing | answer |
| --- | --- | --- |
| a single PUT ignores the bucket's default retention | objects land GOVERNANCE — deletable by the key that wrote them | the lock is an argument of every upload, then read back |
| GOVERNANCE can be bypassed by the writing key | "locked" backups that a stolen key deletes | COMPLIANCE only |
| a multipart upload takes its date from the default rule | every large object fails the read-back, or is locked for the wrong time | `E006`: the default rule must be COMPLIANCE `LOCK_DAYS` days |
| Object Lock can only be enabled at creation | a bucket that can never be immutable | `E005`; create it right ([S3 providers](S3-providers.md)) |
| one PUT stops at 5 GB | `EntityTooLarge` on a large media night | multipart in 64 MiB parts; volumes of at most 4 GiB |
| a delete without a version id only adds a marker | rotation "deletes" and the bill keeps growing | rotation deletes versions, markers included |
| on some providers a key reaches every bucket of its project | one leaked key opens every product's backups | one project per product or client |
| several passes a day | counted by stamp, deploys push out nights still locked | nights are UTC dates read from the name |
| a scheduler recreated around the nightly time fires twice | two passes racing each other's rotation | the once-per-night guard |
| a CI test job inherited the production bot token | the test suite posted to the team chat | notifiers silenced under tests |
| a chat rejects HTML that does not parse | an error containing `<` lost its notification | every value escaped |
| a custom-format dump under a `.sql` name | fed to `psql` during a restore drill, and failed | the suffix names the format; restore reads the magic bytes |
