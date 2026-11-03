# Databases

```python
'DATABASES': {
    'BACKEND': 'offsite_backup.databases.postgres.PostgresSource',
    'ALIASES': ['default'],
    'FORMAT': 'plain',
},
```

PostgreSQL through `pg_dump`. For each alias: `pg_dump | zstd | age` into one file in the
workspace, upload, delete, next. Nothing goes through Python memory.

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
  server holds. Every other backup stays readable off the machine only.
* `keep_apart=True` writes it under `<PREFIX>/released/db-<alias>/` instead, where no
  rotation ever reads — so an alias reused later never mixes two databases' history.

`keep_apart(key)` does the same for a backup already stored: it copies the object under
`<PREFIX>/released/`, locked afresh, and answers the new key. The original stays where its
own rotation will take it.

## Restoring

`backup_restore --apply` and `restore_database()` — see [Restore](Restore.md).

## Other databases

0.1.0 ships PostgreSQL. MySQL (`mysqldump --single-transaction`) and SQLite (the online
backup API) are on the roadmap; meanwhile a subclass of
`offsite_backup.databases.base.DatabaseSource` named in `BACKEND` works without any change
to the package.
