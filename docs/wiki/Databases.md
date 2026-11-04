# Databases

```python
'DATABASES': {
    'BACKEND': 'offsite_backup.databases.postgres.PostgresSource',
    'ALIASES': ['default'],
    'FORMAT': 'plain',
},
```

Three sources, one per engine — each a `BACKEND` of its own:

| engine | backend | dumps with | object suffix |
| --- | --- | --- | --- |
| PostgreSQL | `offsite_backup.databases.postgres.PostgresSource` | `pg_dump` | `.sql.zst.age` / `.dump.zst.age` |
| MySQL, MariaDB | `offsite_backup.databases.mysql.MySQLSource` | `mysqldump` / `mariadb-dump --single-transaction` | `.sql.zst.age` |
| SQLite | `offsite_backup.databases.sqlite.SQLiteSource` | Python's online backup API | `.sqlite3.zst.age` |

For each alias: dump `| zstd | age` into one file in the workspace, upload, delete, next.
Nothing goes through Python memory. Most of this page is PostgreSQL's; what differs for
the others is under [MySQL and MariaDB](#mysql-and-mariadb) and [SQLite](#sqlite).

## Which databases

`ALIASES` names entries of Django's `DATABASES`:

=== "One"

    ```python
    'DATABASES': {'BACKEND': 'offsite_backup.databases.postgres.PostgresSource'},
    ```

    `ALIASES` defaults to `['default']`.

=== "Several"

    ```python
    'DATABASES': {
        'BACKEND': 'offsite_backup.databases.postgres.PostgresSource',
        'ALIASES': ['default', 'analytics'],
    },
    ```

=== "Computed at run time"

    ```python
    'DATABASES': {
        'BACKEND': 'offsite_backup.databases.postgres.PostgresSource',
        'ALIASES': 'acme.tenancy.backup_aliases',
    },
    ```

    A dotted path to a callable that takes no arguments and returns the aliases — for a
    project that adds a database per tenant. It is called at the start of every pass and
    of every morning check. Aliases added to `connections.databases` at run time count.

Each alias is its own **kind**: `db/` for `default`, `db-<alias>/` for every other — a
sibling, not a child, or `db/`'s rotation would count it. Each kind rotates by itself.

**One database that cannot be dumped does not cost the others** — nor the media, nor the
configuration — their night. The pass goes on, uploads the rest, rotates, and then fails
naming every alias that did not go up:

```text
backup_offsite: 1 database(s) not backed up, the rest went up — analytics: PipelineError: `pg_dump` exited with status 1: ...
```

## The connection

From `DATABASES[alias]`: `HOST`, `PORT`, `USER` and `NAME` become `pg_dump` flags;
`PASSWORD` travels in `PGPASSWORD`, **never on the command line** — a command line is
visible in `ps`, and an error text is posted to a chat. `OPTIONS['sslmode']` and
`OPTIONS['sslrootcert']` become `PGSSLMODE` and `PGSSLROOTCERT`. The connection shows up
in `pg_stat_activity` as `application_name = offsite_backup`.

## Formats

| `FORMAT` | `pg_dump` | object suffix | restored with |
| --- | --- | --- | --- |
| `plain` (default) | plain SQL | `.sql.zst.age` | `psql --single-transaction` |
| `custom` | `--format=custom --compress=0` | `.dump.zst.age` | `pg_restore --single-transaction` |

**The suffix names the format.** A custom-format archive under a `.sql.` name gets fed to
`psql` in a hurry, and fails. A restore does not trust the name anyway: it tells the
formats apart by the archive's magic bytes (`PGDMP`), and decompresses whatever starts with
zstd's — so objects written under an older naming scheme restore too.

**Switching from custom-format dumps named `.sql…`.** A project whose own backups were
custom-format dumps under a `.sql.zst.age` name, and which now sets `FORMAT: 'custom'`, sees
both names in a database's folder for one retention window — the old `.sql.zst.age`
objects, the new `.dump.zst.age` ones — until the old ones rotate out. That is expected and
harmless: rotation reads nights from the stamps, not from the suffix, and a restore
detects the format from the magic bytes. There is no option to keep the old suffix: a
suffix that does not name the format is how a custom dump ends up fed to `psql`.

The custom format is dumped with `pg_dump`'s own compression **off** and compressed by
zstd: zstd compresses better, and compressed data does not compress twice. It is what you
want when you need `pg_restore --list` (an integrity read of the whole table of contents,
which `backup_restore` runs) or to restore one table from a dump. Plain SQL is readable with
`zstd -dc | less` and loads into anything that speaks SQL.

By default both carry `--no-owner --no-privileges`, so a dump restores into a database
owned by a different role. `NO_OWNER: False` keeps ownership and grants. `EXTRA_ARGS` is
appended to the command — `['--exclude-table-data=django_session']`, for example.

## How a dump is known to be whole

The pipeline runs with `pipefail` semantics and no shell: every stage's exit status is read,
so a `pg_dump` that dies half way fails the pass instead of leaving a small, valid,
truncated `.age`. The tail of the failing stage's stderr goes into the error.

A restore checks again: a plain dump must end with `PostgreSQL database dump complete`; a
custom one must pass `pg_restore --list`.

## Versions

`pg_dump` refuses to dump a server newer than itself. `python manage.py check --deploy`
connects to each alias and compares:

```text
?: (offsite_backup.E004) pg_dump is 16, older than the server of 'default' (18).
	HINT: Install the PostgreSQL 18 client (e.g. postgresql-client-18) in the image.
```

An alias it cannot connect to is skipped by this check rather than reported.

## A one-off backup of one database

For the moment a database is about to go — removed, put to sleep, handed back:

```python
from offsite_backup import back_up_database

key = back_up_database('tenant_42')
```

It raises when backups are not configured, so a removal that cannot be backed up does
not happen. The dump goes to `<PREFIX>/db-tenant_42/` like a nightly one; nothing rotates
that folder once the alias is no longer among the pass's targets.

* `extra_recipients=['age1...']` seals this one dump to more keys as well — for a database
  the server itself must be able to restore later (waking a tenant), seal it to a key the
  server holds. Every other backup stays readable off the machine only. Restoring it on
  the server: `restore_database(..., identity_text=...)` ([Restore](Restore.md#3-load-a-database)).
* `keep_apart=True` writes it under `<PREFIX>/released/db-<alias>/` instead, where no
  rotation ever reads — so an alias reused later never mixes two databases' history.

`keep_apart(key)` does the same for a backup already stored: it copies the object under
`<PREFIX>/released/`, locked afresh, and answers the new key. The original stays where its
own rotation will take it.

## Restoring

`backup_restore --apply` and `restore_database()` — see [Restore](Restore.md).

## MySQL and MariaDB

```python
'DATABASES': {
    'BACKEND': 'offsite_backup.databases.mysql.MySQLSource',
    'ALIASES': ['default'],
    'CLIENT': 'mariadb',   # 'mysql' (default): mysqldump and mysql
},
```

`ALIASES` works as for PostgreSQL. Each dump is

```text
mysqldump --defaults-extra-file=<0600 file> --single-transaction --quick --hex-blob --triggers
          --no-tablespaces --default-character-set=utf8mb4 [--set-gtid-purged=OFF] <EXTRA_ARGS> <NAME>
```

* **One moment of the database.** `--single-transaction` reads every InnoDB table as of
  the dump's start, without locking a table against the application. A MyISAM table is not
  covered by it — it is read as it is while the dump runs; convert it, or accept that.
* **The password never reaches argv**, nor `MYSQL_PWD` (deprecated by MySQL): `HOST`
  (a path starting with `/` is a socket), `PORT`, `USER`, `PASSWORD`,
  `OPTIONS['unix_socket']` and `OPTIONS['ssl']` go into an option file readable by this
  process only, passed as `--defaults-extra-file` and removed when the dump ends.
* `--set-gtid-purged=OFF` (MySQL only, which `mariadb-dump` does not know): a dump carrying
  the server's GTID set refuses to load into any other server.
* `--no-tablespaces` spares the dump user the `PROCESS` privilege. Routines and events are
  not dumped by default — Django does not create them; `'EXTRA_ARGS': ['--routines',
  '--events']` adds them for a project that does.
* **Whole**: a restore checks the dump ends with `-- Dump completed`, the line both programs
  write last (so do not pass `--skip-comments`).
* **No Python driver needed**: the source runs only the client programs. A restore asks the
  server through `mysql` / `mariadb` whether the target holds any table, refuses one that
  does unless `--clean` — which drops every table and view first, foreign key checks off —
  then feeds the dump to the client, which stops at the first error.

The client should be at least as new as the server: `mysql-client` for MySQL,
`mariadb-client` for MariaDB (`mysqldump` 8 against a MariaDB server trips on statistics
tables MariaDB does not have).

## SQLite

```python
'DATABASES': {
    'BACKEND': 'offsite_backup.databases.sqlite.SQLiteSource',
    'ALIASES': ['default'],
},
```

Each alias's `NAME` is a file. It is copied with `sqlite3.Connection.backup` — the online
backup API — into the pass's private workspace, then compressed and sealed from there and
the copy removed. The backup API copies page by page and starts over when a writer touches
a page it has copied, so the copy is **one moment** of the database while the application
keeps writing; copying the file itself could catch a transaction half way, or miss what
only the write-ahead log (`-wal`) holds. No binary is needed.

* The workspace holds one plain copy of the database for the length of its sealing — mind
  `WORKDIR` on a machine whose temporary directory is small or shared.
* A database file that does not exist fails that database (`SourceMissingError`) instead
  of creating an empty one; `check --deploy` reports it as `W005`, or `E009` with
  `'REQUIRED': True`. An alias that is not SQLite, or an in-memory database, is `E001`.
* **Verified** on restore: an SQLite header and `PRAGMA integrity_check` answering `ok`.
* **Restored** with the same backup API over the target alias's file, which must hold no
  table unless `--clean`. Close the application's connections to it first.

## Other databases

A subclass of `offsite_backup.databases.base.DatabaseSource` named in `BACKEND` works
without any change to the package: `targets()` (the `ALIASES` logic is the base class's),
`suffix()`, `dump()`, `verify()` and `restore()`.
