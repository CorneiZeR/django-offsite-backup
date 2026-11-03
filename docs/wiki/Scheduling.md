# Scheduling

Two jobs, both in UTC:

| job | when | what |
| --- | --- | --- |
| the nightly pass | 03:00 UTC | everything, media included; guarded to once per night |
| the morning check | 09:00 UTC | is there a whole backup younger than `STALE_HOURS`? — [Monitoring](Monitoring.md) |

Object names, nights and the guard are all UTC, whatever `TIME_ZONE` says. Run the
morning check a few hours after the pass, so a slow night is not reported missing, and
early enough in your working day that someone reads it.

## Celery

```python
# settings.py
from offsite_backup.schedule.celery import beat_schedule

CELERY_TIMEZONE = 'UTC'
CELERY_IMPORTS = ['offsite_backup.schedule.celery']
CELERY_BEAT_SCHEDULE = {
    **beat_schedule(),                 # nightly 03:00, check 09:00
    # **beat_schedule(nightly=(2, 30), check=(8, 0)),
    ...
}
```

`offsite_backup.schedule.celery` defines two shared tasks, `offsite_backup.nightly`
(`run_nightly()`) and `offsite_backup.check` (`check_last_night()`), and
`beat_schedule(nightly=(3, 0), check=(9, 0))` returns beat entries for both as
`(hour, minute)` crontabs **in the beat's timezone** — hence `CELERY_TIMEZONE = 'UTC'`, or
shift the hours. `CELERY_IMPORTS` makes sure the worker registers the tasks; this
assumes the usual `app.config_from_object('django.conf:settings', namespace='CELERY')`.

The module imports Celery, so only a project that runs Celery imports it.

The check task **raises** when a backup is missing or the store cannot be read, so the
task fails wherever your Celery failures are reported, on top of the chat message.

## taskiq

```python
# acme/broker.py
from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource

from offsite_backup.schedule.taskiq import register

broker = ...  # your broker
backup_nightly, backup_check = register(broker)
# register(broker, nightly='30 2 * * *', check='0 8 * * *')

scheduler = TaskiqScheduler(broker, sources=[LabelScheduleSource(broker)])
```

`register(broker, *, nightly='0 3 * * *', check='0 9 * * *')` defines the same two tasks,
named `offsite_backup.nightly` and `offsite_backup.check`, with `schedule` labels for
taskiq's `LabelScheduleSource` (cron, UTC). The pass is a blocking batch job, so each task
runs it in a thread. The worker must have Django set up before the tasks run, as for any
task that touches the ORM.

## cron, systemd timers, Kubernetes CronJobs

Anything that runs a command on a schedule:

```cron
CRON_TZ=UTC
0 3 * * *  cd /app && python manage.py backup_offsite --reason nightly
0 9 * * *  cd /app && python manage.py backup_check
```

The command is **not** guarded (below) — cron fires once, so it does not need to be. Both
commands exit non-zero on failure, so cron's mail, a systemd `OnFailure=` unit or the
CronJob's failed status sees it as well as the chat.

With Docker Compose and a host crontab:

```cron
0 3 * * *  cd /srv/acme && docker compose exec -T worker python manage.py backup_offsite --reason nightly
```

## Once per night

A deploy that recreates the scheduler around the nightly time **fired the nightly task
twice** — the old scheduler as it stopped, the new one as it started. Two passes in one
night double the upload and race each other's rotation. So `run_nightly()` — what both
adapters call — claims the UTC night in a Django cache every worker shares:

1. a night already **done** is skipped;
2. otherwise it **claims** `running` with an atomic `cache.add` (held for
   `RUNNING_SECONDS`, an hour); a night someone else is running is skipped;
3. it looks for **done** again under the claim, in case it finished in between;
4. on **failure** it releases the claim and re-raises, so the scheduler's retry can run
   tonight;
5. on **success** it writes `done` (kept `DONE_SECONDS`, 26 hours — past the end of its UTC
   date) **before** releasing `running`, so there is no moment when neither is set.

A pass whose worker died holds the night for an hour at most. The keys are
`offsite_backup:<PREFIX>:<YYYY-MM-DD>:done` and `:running`.

```python
'GUARD': {'CACHE': 'default'},   # ENABLED True, RUNNING_SECONDS 3600, DONE_SECONDS 93600
```

The cache **must be shared** between the workers and the schedulers: Redis, Memcached, the
database cache. A `LocMemCache` or `DummyCache` is per process and guards nothing; `W003`
says so. A `CACHE` alias missing from `CACHES` is `E001`. `'GUARD': {'ENABLED': False}`
turns the claim off.

Passes by hand and deploy passes are never guarded: they are meant to run whenever
someone asks.

## Calling it yourself

```python
from offsite_backup import run, run_nightly, check_last_night

run_nightly()  # guarded; None when skipped or when backups are off
run(reason='manual')  # one pass now; Summary, or raises
run(media=False, reason='deploy')
run(only=['db'])  # 'db', 'media', 'config'
check_last_night()  # oldest of the newest whole backups; raises when missing
```

`run()` raises `NotConfiguredError` on a machine where backups are off; the commands and
`run_nightly()` check first and do nothing there.
