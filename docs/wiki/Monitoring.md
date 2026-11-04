# Monitoring

A failed pass says so. The night that **never ran** — the scheduler stopped, the worker
was never deployed, the machine was off — says nothing at all. The morning check is the
answer to that silence.

## The morning check

```shell
python manage.py backup_check
# backup_check: ok, newest whole backup 2026-03-10 03:00 UTC
```

It asks the store for the newest **whole** backup under:

* the `config/` folder, when `CONFIG` is set, **and**
* every database's folder — `db/`, `db-<alias>/` for each alias the pass would dump today,
* or, with neither configured, every media folder.

Objects marked `.without-media.` do not count: a deploy at noon cannot stand in for a
night that never ran. Media does not count either while there are databases or a
configuration: tonight's media does not stand in for a database that was not written.

| finding | message | exit |
| --- | --- | --- |
| every folder has a whole backup within `STALE_HOURS` (26) | none | 0 |
| a database with no backup at all, beside databases that are backed up | none — logged at INFO, `Databases not backed up yet` ([below](#a-database-added-today)) | 0 |
| some folder's newest is older, or there is none | **MISSING**, naming the oldest | non-zero (`BackupMissingError`) |
| the store could not be read | **UNCHECKED** — as loud as missing | non-zero (`BackupUncheckedError`) |
| backups are off on this machine | none | 0, "nothing to do" |

The error names every stale folder:

```text
CommandError: backup_check: No whole backup within 26 h under acme/prod/: db-analytics/ (none), config/ (2026-03-08 03:00 UTC)
```

`STALE_HOURS` is 26 so that a pass at 03:00 one night and a slow one the next still pass
a check at 09:00. Raise it if your nightly pass runs less often than daily.

### A database added today

With `ALIASES` a callable, the list of databases is computed at every check — and a tenant
created after the nightly pass has no backup at all at the morning check. That is not a
missed night, so:

* a database folder with **no backup at all**, beside databases that are backed up, is
  "not backed up yet": logged at INFO (`Databases not backed up yet`, the kinds in
  `ob_kinds`), not MISSING. Its first nightly pass backs it up;
* a database whose backups **exist but are stale** is MISSING, as before;
* the **only** database, with no backup at all, is still MISSING — there is nothing beside
  it to show that the passes run;
* a database whose dump **fails every night** is not silent either: each night's FAILED
  message names it.

Schedule it as in [Scheduling](Scheduling.md); from Python it is
`offsite_backup.check_last_night()`, which returns the oldest of the newest whole backups
(or `None` where backups are off) and raises the same errors.

## The last-success file

```python
'LAST_SUCCESS_FILE': '/var/run/acme/backup-last-success',
```

After every successful pass the file is rewritten with the pass's time in ISO format:

```text
2026-03-10T03:00:12.402113+00:00
```

Something already watching the machine can read its age — a node-exporter textfile
collector, a Docker healthcheck, a monitoring agent's file-age check. It is written by
**every** successful pass, deploy passes and passes by hand included, so it answers "did
anything succeed lately", not "did last night run"; the morning check answers that. A file
that cannot be written is logged (`Last-success marker not written`) and does not fail the
pass. Mount the path where your monitor can see it — the worker writes it at night, the
deploy's one-off container before a migration.

A metrics endpoint is on the roadmap.

## The log

Everything is logged to the `offsite_backup` logger, with fields in the record's `extra`
(prefixed `ob_`, so a JSON formatter can keep them apart from your own):

| level | message | fields |
| --- | --- | --- |
| INFO | `Backup written` | `ob_prefix`, `ob_objects`, `ob_bytes` |
| INFO | `Database backed up` (a one-off backup) | `ob_alias`, `ob_key` |
| INFO | `Nightly backup skipped: ...` (off, done, or being done) | — |
| INFO | `Databases not backed up yet` (the morning check) | `ob_kinds` |
| ERROR (with traceback) | `Database not backed up` | `ob_alias` |
| ERROR (with traceback) | `Store not written; left out of this pass` — one of [several stores](Stores-several.md) refused an object | `ob_store` |
| WARNING | `Backup still locked; left for a later night` | `ob_key`, `ob_until` |
| WARNING | `Backup depth not read` | `ob_folder` |
| WARNING | `Last-success marker not written` | `ob_path`, `ob_error` |
| WARNING | `Media file not listed`, `Media file not readable; left out` | `ob_path`, `ob_error` |
| WARNING | `Configuration path not found; left out`, `Media directory not found` — a mount missing where the pass ran (`W005` under `check --deploy`) | `ob_path` |
| WARNING | `Media volume not found` — a `VolumeSource` Docker volume that does not exist (`W005` under `check --deploy`) | `ob_volume` |
| WARNING | `Media file is a hard link; left out` — in a Docker volume | `ob_path` |
| ERROR | `Notifier not built`, `Notifier raised` | `ob_notifier`, `ob_error` |
| ERROR | `Backup message not formatted` | `ob_error` |
| ERROR | `Telegram refused the backup message` | `ob_status`, `ob_description` |
| ERROR | `Telegram unreachable`, `Backup message not sent through the bot` | `ob_error` (the class only) |
| INFO / ERROR | `Backup written` / `Backup failed` ... from `LogNotifier` | `ob_prefix`, `ob_text` |

No message, token, password or private key is ever logged.

## Heartbeats of scheduled tasks

A project that tracks when each of its scheduled tasks last ran — a heartbeat table, a
"last run" dashboard — sees the package's two tasks as **never run** until each has run
once: the nightly pass after its first 03:00, the check after its first 09:00. Register
them under their task names, the same for Celery and taskiq:

| task | default schedule (UTC) |
| --- | --- |
| `offsite_backup.nightly` | 03:00 |
| `offsite_backup.check` | 09:00 |

## What the store holds

`python manage.py backup_list` prints, per kind, the nights kept, the oldest night, the
objects and bytes, the newest stamp, how long the newest object is locked and how many
objects came from passes without media. `--json` for scripts. See
[Restore](Restore.md#1-see-what-there-is).
