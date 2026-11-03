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
| some folder's newest is older, or there is none | **MISSING**, naming the oldest | non-zero (`BackupMissingError`) |
| the store could not be read | **UNCHECKED** — as loud as missing | non-zero (`BackupUncheckedError`) |
| backups are off on this machine | none | 0, "nothing to do" |

The error names every stale folder:

```text
CommandError: backup_check: No whole backup within 26 h under acme/prod/: db-analytics/ (none), config/ (2026-03-08 03:00 UTC)
```

`STALE_HOURS` is 26 so that a pass at 03:00 one night and a slow one the next still pass
a check at 09:00. Raise it if your nightly pass runs less often than daily.

Note that the database list is computed: with `ALIASES` a callable, a database added today
is reported missing until its first nightly pass.

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
| ERROR (with traceback) | `Database not backed up` | `ob_alias` |
| WARNING | `Backup still locked; left for a later night` | `ob_key`, `ob_until` |
| WARNING | `Backup depth not read` | `ob_folder` |
| WARNING | `Last-success marker not written` | `ob_path`, `ob_error` |
| WARNING | `Media file not listed`, `Media file not readable; left out` | `ob_path`, `ob_error` |
| ERROR | `Notifier not built`, `Notifier raised` | `ob_notifier`, `ob_error` |
| ERROR | `Backup message not formatted` | `ob_error` |
| ERROR | `Telegram refused the backup message` | `ob_status`, `ob_description` |
| ERROR | `Telegram unreachable`, `Backup message not sent through the bot` | `ob_error` (the class only) |
| INFO / ERROR | `Backup written` / `Backup failed` ... from `LogNotifier` | `ob_prefix`, `ob_text` |

No message, token, password or private key is ever logged.

## What the store holds

`python manage.py backup_list` prints, per kind, the nights kept, the oldest night, the
objects and bytes, the newest stamp, how long the newest object is locked and how many
objects came from passes without media. `--json` for scripts. See
[Restore](Restore.md#1-see-what-there-is).
