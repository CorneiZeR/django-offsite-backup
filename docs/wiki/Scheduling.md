# Scheduling

Two jobs, both in UTC — and a third, monthly, when you ask for it:

| job | when | what |
| --- | --- | --- |
| the nightly pass | 03:00 UTC | everything, media included; guarded to once per night |
| the morning check | 09:00 UTC | is there a whole backup younger than `STALE_HOURS`? — [Monitoring](Monitoring.md) |
| the restore drill | off unless scheduled; e.g. the 1st, 05:00 UTC | restore the newest whole backup into a throwaway database and report — [Restore](Restore.md#the-monthly-drill) |

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
`(hour, minute)` crontabs **in the beat's timezone**. `CELERY_IMPORTS` makes sure the worker
registers the tasks; this assumes the usual
`app.config_from_object('django.conf:settings', namespace='CELERY')`.

### The beat's timezone

The entries are in whatever `CELERY_TIMEZONE` says, not in UTC. A project whose beat runs in
local time has two ways to keep the pass at 03:00 UTC:

=== "Run the beat in UTC"

    ```python
    CELERY_TIMEZONE = 'UTC'
    CELERY_BEAT_SCHEDULE = {**beat_schedule(), ...}
    ```

    Simple, and it **shifts every other entry** of the beat too: a report scheduled for
    `crontab(hour=8)` local time now runs at 08:00 UTC. Rewrite the project's own entries
    in UTC when you switch.

=== "Pass local hours"

    ```python
    CELERY_TIMEZONE = 'Europe/Berlin'   # UTC+2 in summer
    CELERY_BEAT_SCHEDULE = {
        **beat_schedule(nightly=(5, 0), check=(11, 0)),   # 03:00 and 09:00 UTC in summer
        ...
    }
    ```

    The other entries stay as they are. A timezone with daylight saving moves the pass by an
    hour twice a year — harmless: the guard and the nights are UTC dates either way, and
    `STALE_HOURS` (26) absorbs the hour.

### Retries

The nightly task retries on any error: `autoretry_for=(Exception,)`, `max_retries=3`
(`offsite_backup.schedule.celery.NIGHTLY_RETRIES`), `retry_backoff=60` — a minute, then two,
then four. Object storage is reached over the network, and one transient error should not
lose the night. To turn the retries off:

```python
CELERY_TASK_ANNOTATIONS = {'offsite_backup.nightly': {'max_retries': 0}}
```

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

`register(broker, *, nightly='0 3 * * *', check='0 9 * * *', retries=3)` defines the same
two tasks, named `offsite_backup.nightly` and `offsite_backup.check`, with `schedule` labels
for taskiq's `LabelScheduleSource` (cron, UTC). The pass is a blocking batch job, so each
task runs it in a thread. The worker must have Django set up before the tasks run, as for
any task that touches the ORM.

### Retries

The nightly task carries the labels `retry_on_error=True` and `max_retries=retries` (3 by
default). Labels only describe the wish: **the broker needs taskiq's
`SimpleRetryMiddleware`** to act on them.

```python
from taskiq.middlewares import SimpleRetryMiddleware

broker = YourBroker(...).with_middlewares(SimpleRetryMiddleware())
backup_nightly, backup_check = register(broker)
# register(broker, retries=0)   # no retry labels at all
```

## cron, systemd timers, Kubernetes CronJobs

Anything that runs a command on a schedule:

```cron
CRON_TZ=UTC
0 3 * * *  cd /app && python manage.py backup_offsite --reason nightly
0 9 * * *  cd /app && python manage.py backup_check
```

The command is **not** guarded (below) — cron fires once, so it does not need to be — and
not retried. Both
commands exit non-zero on failure, so cron's mail, a systemd `OnFailure=` unit or the
CronJob's failed status sees it as well as the chat.

On a server with no Django project, the same jobs are `offsite-backup run --reason nightly`
and `offsite-backup check` — [Without Django](Plain-servers.md#scheduling).

With Docker Compose and a host crontab:

```cron
0 3 * * *  cd /srv/acme && docker compose exec -T worker python manage.py backup_offsite --reason nightly
```

## The monthly drill

Off by default in both adapters — it needs a throwaway database (`DRILL['DATABASE']`) and the
private key where it runs; read [where it runs](Restore.md#where-it-runs-the-identity) first.

```python
# Celery: the 1st of the month, 05:00 in the beat's timezone — (day of month, hour, minute)
CELERY_BEAT_SCHEDULE = {**beat_schedule(drill=(1, 5, 0)), ...}

# taskiq: a cron expression, UTC
backup_nightly, backup_check = register(broker, drill='0 5 1 * *')
# the drill task: broker.find_task('offsite_backup.drill')
```

The task is `offsite_backup.drill` (`run_drill()`). It is **not retried**: a drill that
failed has said why, and another attempt would restore the same backup again. It skips
where backups are off, and otherwise raises on failure, so the scheduler's own failure
report fires beside the chat's. Without a scheduler:

```cron
0 5 1 * *  cd /app && python manage.py backup_drill
```

## Retries and the guard

Both adapters retry the **nightly** task only. A failed pass gives the night back to the
guard (below), so a retry re-enters through it and runs the pass again; a pass that
succeeds marks the night done once. Each attempt that fails sends its own FAILED message, so
a transient error the retries absorbed shows as one or more FAILED messages followed by a
written one.

The **morning check** is not retried: each attempt would only say MISSING again.

## Once per night

A deploy that recreates the scheduler around the nightly time **fired the nightly task
twice** — the old scheduler as it stopped, the new one as it started. Two passes in one
night double the upload and race each other's rotation. So `run_nightly()` — what both
adapters call — claims the UTC night where every worker sees it. `GUARD['BACKEND']` says
where: a shared Django cache (`'cache'`, the default) or a PostgreSQL advisory lock
(`'database'`).

### The `cache` backend

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

### The `database` backend

```python
'GUARD': {'BACKEND': 'database'},   # DATABASE 'default'
```

For a project with no shared cache — one that would otherwise configure Redis for the guard
alone. The databases this package dumps are PostgreSQL, so a project that has them already
has what the lock needs: no new dependency, and a database every worker reaches is shared by
construction.

1. **running** is a PostgreSQL *session* advisory lock (`pg_try_advisory_lock`) on
   `GUARD['DATABASE']` (default `'default'`), with a key derived from `PREFIX` — so two
   projects sharing one database server do not block each other. A night someone else is
   running is skipped.
2. **done** is asked of the store itself: under the lock, a whole backup stamped with
   tonight's UTC date in `config/` **and in every database's folder** means the night is
   done, and it is skipped. No cache at all. (With neither config nor databases, the media
   folders answer.)
3. On failure or success the lock is released. A pass where one database failed still
   uploads the others and the config — but that database's folder has nothing for tonight,
   so the retry runs the pass again.

There is no `RUNNING_SECONDS`: a worker that dies mid-pass closes its connection, and the
server releases the lock at that moment. `GUARD['DATABASE']` must be a PostgreSQL alias of
`DATABASES` (`E001` otherwise, and for an unknown `BACKEND`); `W003` does not apply.
`CACHE`, `RUNNING_SECONDS` and `DONE_SECONDS` are not read.

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
