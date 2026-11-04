# Restore

A backup nobody has restored is a hope. This page is the procedure, and a drill to run
every month so the procedure is known to work before the day it is needed.

## What you need

* The **private key** — from your password manager. Never copy it to the production
  server for a routine restore; restore from your own machine, or from a throwaway one.
* A machine with the project's image (or a checkout with the package installed), `age`,
  `zstd`, and `psql`/`pg_restore` of the server's major version or newer.
* Settings that point at the same store and `PREFIX`. A **read-only** S3 key is enough and
  better than the server's.

The private key is given as a file (`--identity FILE`) or in the `AGE_IDENTITY`
environment variable — never as an argument, which `ps` would show. From the variable it is
written to a `0600` file in a private temporary directory for `age`, and removed with it.

## 1. See what there is

```shell
python manage.py backup_list
```

```text
acme/prod/
  config/: 14 nights since 2026-02-25, 19 objects, 0.4 MB, newest 2026-03-10T14-12Z, locked until 2026-03-23T14:12:31+00:00, 5 without media
  db/: 14 nights since 2026-02-25, 19 objects, 410.2 MB, newest 2026-03-10T14-12Z, locked until 2026-03-23T14:12:31+00:00, 5 without media
  media/: 14 nights since 2026-02-25, 28 objects, 74322.0 MB, newest 2026-03-10T03-00Z, locked until 2026-03-23T03:02:10+00:00
```

`--kind db` narrows it (repeat for several); `--json` is for scripts.

## 2. Download and decrypt

```shell
export AGE_IDENTITY="$(cat ~/secure/backup-acme-prod.key)"      # or --identity FILE
python manage.py backup_restore --stamp latest --into ./restore
```

```text
restore/config/2026-03-10T03-00Z.tar
restore/db/2026-03-10T03-00Z.sql  (verified)
restore/media/2026-03-10T03-00Z.001.tar
restore/media/2026-03-10T03-00Z.002.tar
```

* `--stamp latest` is the newest **whole** backup (the newest night in `config/`, or in
  `db/` without a configuration archive); a pass without media is not "whole". Give a
  stamp such as `--stamp 2026-03-10T14-12Z` to take any pass, a deploy's included.
* Every object of that stamp is downloaded, decrypted and — if it starts with zstd's magic
  bytes — decompressed, into `<into>/<kind>/`. `--into` defaults to `./restore-<stamp>`.
* `--kind db` (repeatable) takes only some kinds — skip a night's media when you need the
  database.
* Every database dump is **verified**: a plain dump must end with
  `PostgreSQL database dump complete`; a custom-format one must pass
  `pg_restore --list`. A dump that fails says so and stops.
* Backups copied under `<PREFIX>/released/` are not part of a stamp; restore those with
  `--apply` and the full key (below).

What you get:

| kind | file | open with |
| --- | --- | --- |
| `db`, `db-<alias>` | `<stamp>.sql` (plain) or `<stamp>.dump` (custom) | `psql`, `pg_restore` |
| `media`, `media-<name>` | `<stamp>.<NNN>.tar`, one per volume | `tar -xf` — each volume on its own |
| `config` | `<stamp>.tar` | `tar -xf`; `rendered.env` is the environment |

**A custom-format dump under a legacy `.sql…` name** — written by a predecessor that named
its custom dumps `.sql.zst.age` — just works: the format is read from the magic bytes
(`PGDMP`), not from the name. It is verified with `pg_restore --list` and loaded with
`pg_restore` by `--apply`. The file `--into` writes keeps the object's name (`<stamp>.sql`),
so open it with `pg_restore`, not `psql`; `head -c 5` on it prints `PGDMP` for a custom dump.

## 3. Load a database

```shell
python manage.py backup_restore --stamp latest --apply --database default
```

* `--apply --database ALIAS` downloads, decrypts, verifies and loads the dump into that
  `DATABASES` alias — with `psql --single-transaction` for plain SQL, `pg_restore
  --single-transaction --exit-on-error` for the custom format, both `--no-owner`.
* With `--apply`, `--stamp latest` means the newest dump **of that database**, a deploy's
  included — the most recent state is what you want back.
* `--stamp` also takes a full key: `--stamp acme/prod/released/db-tenant_42/2026-03-01T10-00Z.sql.zst.age`.
* `--from-database ALIAS` loads *another* alias's backup — production's `default` into a
  scratch database:

    ```shell
    python manage.py backup_restore --stamp latest --apply --database scratch --from-database default
    ```

* The target must be **empty**. `--clean` replaces a non-empty one: it runs
  `DROP SCHEMA public CASCADE; CREATE SCHEMA public;` first. Objects in other schemas are
  not dropped.

From Python:

```python
from offsite_backup import restore_database

restore_database('latest', 'scratch', identity='~/secure/backup-acme-prod.key', source_alias='default')
restore_database('2026-03-10T03-00Z', 'default', clean=True)  # identity from AGE_IDENTITY
```

`restore_database(source, alias, *, identity=None, clean=False, source_alias=None,
identity_text=None)` answers with the key it loaded.

`identity_text` is the private key's **text**, for code that holds it rather than a file:
it is written to a private `0600` temporary file for `age` and removed afterwards. It is
meant for a dedicated, narrowly used identity the server holds on purpose — the wake key
of [a one-off backup](Databases.md#a-one-off-backup-of-one-database) — **never the backups'
main key**, which stays off the server. Passing both `identity` and `identity_text` is
refused (`MisconfiguredError`).

```python
restore_database(key, 'tenant_42', identity_text=settings.WAKE_IDENTITY)
```

## 4. Media and configuration

```shell
mkdir -p media && for v in restore/media/*.tar; do tar -xf "$v" -C media; done
mkdir -p deploy && tar -xf restore/config/*.tar -C deploy
```

Names in a media volume are relative to the source's `ROOT` (or are the full object keys,
for an S3 source). Copy them back into the volume, directory or bucket the application
reads. `rendered.env` is the environment the release ran with — the file to rebuild `.env`
from on a new machine.

## Rebuilding a lost machine

1. Provision the machine; install Docker.
2. `backup_restore --stamp latest --into ./restore` on your own machine.
3. Unpack the configuration archive and copy its deployment directory to the new machine;
   rebuild `.env` from `rendered.env` (and rotate any secret you suspect left with the old
   machine).
4. Start the database; `backup_restore --stamp latest --apply --database default` from
   inside the new deployment, with `AGE_IDENTITY` set for that one command only — or load
   the dump you already decrypted in step 2 with `psql`/`pg_restore`, and the private key
   never touches the server.
5. Unpack the media volumes into the media volume or bucket.
6. Start the application; run `backup_offsite --reason manual` and check the message.

## The monthly drill

Restoring is a skill that decays, and a backup that was never restored is a hope. The drill
does what a person would, end to end, and says the outcome where the backups are said:

```shell
python manage.py backup_drill                     # or: offsite-backup drill
python manage.py backup_drill --without-media     # the volumes are the slow part
```

1. The newest **whole** pass is found (as `--stamp latest`) and every object of it
   downloaded and decrypted — the identity from `--identity FILE`, `DRILL['IDENTITY_FILE']`
   or `AGE_IDENTITY`.
2. Every database dump is verified, then restored into **`DRILL['DATABASE']`**, a throwaway
   database the project names — emptied first (every schema but the system ones dropped),
   loaded, and every table counted there with `count(*)`.
3. With `DRILL['COMPARE_LIVE']` (on by default), the live database is counted too, and the
   message shows both. Rows are compared, not required to match: production kept writing
   after the backup. A table the live database has and the restore lacks is named
   (`⚠️ Not in the backup, live now: ...`) — a migration since the pass, or a hole — without
   failing the drill. A live database that cannot be counted is logged and left uncompared.
4. Every media volume and the configuration archive is decrypted and read to its end as a
   tar (unless `DRILL['MEDIA']` is false or `--without-media` is given) — files and bytes
   counted.
5. The throwaway database is emptied again and the workspace removed, whatever happened.
6. The outcome goes through the notifiers: **Restore drill passed** with a line per database
   and per kind of archive, or **Restore drill FAILED** naming every object that did not
   come back and the step it failed at ([Notifications](Notifications.md#what-the-messages-look-like)).
   One database that does not restore does not stop the others; the command exits
   non-zero at the end, naming each.

```python
OFFSITE_BACKUP = {
    ...,
    'DRILL': {
        'DATABASE': 'drill',                          # an alias in DATABASES (or CONNECTIONS off Django)
        'IDENTITY_FILE': '/run/secrets/drill.key',    # or AGE_IDENTITY, or --identity
        # 'MEDIA': False,                             # leave the volumes unopened
        # 'COMPARE_LIVE': False,                      # never count the live database
    },
}
DATABASES['drill'] = {**DATABASES['default'], 'HOST': 'scratch-db', 'NAME': 'drill'}
```

**Never the live database.** The drill refuses a target that is a database it backs up —
the same alias, or another alias with the same host (`localhost`, `127.0.0.1` and `::1` are
one), port (an empty one is `5432`) and name — before it reads the store, and again for every
database the pass wrote. `check --deploy` reports that, or a target that names no database
at all, as `E011`, before the month it would run. What the drill does to its target is
destructive by design: point it at a database that holds nothing you want.

From Python, `offsite_backup.run_drill(identity=None, identity_text=None, database=None,
media=None)` returns the report (`stamp`, `target`, `databases`, `archives`, `seconds`) and
raises `DrillFailedError` naming what failed.

### Where it runs: the identity

The drill needs the **private key**, which by design is not on the production server
([Security model](Security-model.md#the-private-key)). Two ways to run it, in order of
preference:

* **On a machine of its own** — a small drill host or a CI job with the identity, a
  **read-only** store key and a scratch PostgreSQL. It needs no copy of the project: the
  [command line](Plain-servers.md) runs the drill from a TOML setting with the same
  `PREFIX`, `STORE` and `ENCRYPTION`, a `DATABASES` block whose `CONNECTIONS` hold the
  drill's alias, and `COMPARE_LIVE` off (the live database is out of reach there).
* **In the project's scheduler** — `beat_schedule(drill=...)` or `register(broker, drill=...)`
  ([Scheduling](Scheduling.md#the-monthly-drill)). Simple, and it compares with the live
  database — but the worker then holds a key that opens every backup sealed to it. Accept
  that only knowingly; seal the backups to a second recipient whose key exists only for
  the drill, so it can be rotated on its own.

### A database source of your own

The drill asks the database source two more things than a pass does: `tables(alias)`, every
table with its row count, and `clear(alias)`, empty the throwaway database. A source that
does not implement them fails the drill by its name, and the rest still runs.

### By hand

The steps the command automates, for a machine that cannot run it:

1. `backup_restore --stamp latest --into ./drill`; note the stamp and that every dump says
   `(verified)`.
2. Start a clean PostgreSQL of the **same major** as production
   (`docker run -d --name drill -e POSTGRES_PASSWORD=drill -p 55432:5432 postgres:18`).
3. Load the dump into it — `psql`/`pg_restore`, or
   `backup_restore --stamp <stamp> --apply --database drill --from-database default`.
4. Count every table in both databases and compare:

    ```sql
    SELECT format('SELECT %L AS t, count(*) FROM %I.%I', table_name, table_schema, table_name)
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name \gexec
    ```

5. Unpack one media volume and open a few files; unpack the configuration archive and read
   `rendered.env` — is every variable the application needs there?
6. Write down the date, the stamp, the table count and how long it took. Delete the drill
   database and `./drill`.
