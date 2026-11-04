"""Celery tasks and a beat schedule. Imported only by a project that uses Celery.

``CELERY_BEAT_SCHEDULE = {**beat_schedule(), ...}`` — nightly 03:00, check 09:00, in the
beat's timezone (``CELERY_TIMEZONE``; UTC recommended).

The nightly task retries on any error, 3 times with exponential backoff from a minute
(``CELERY_TASK_ANNOTATIONS = {'offsite_backup.nightly': {'max_retries': 0}}`` turns it
off). A failed pass gives the night back to the guard, so the retry re-enters it. The
morning check is not retried: every attempt would say MISSING again.

The monthly restore drill is a task too (``offsite_backup.drill``), scheduled only when
asked: ``beat_schedule(drill=(1, 5, 0))`` — the 1st of the month, 05:00. It is not retried:
a drill that failed has said why, and another attempt would restore the same backup again.
"""

from typing import Any

from celery import shared_task
from celery.schedules import crontab

__all__ = ('NIGHTLY_RETRIES', 'backup_check', 'backup_drill', 'backup_nightly', 'beat_schedule')

NIGHTLY_RETRIES = 3


@shared_task(
    name='offsite_backup.nightly',
    autoretry_for=(Exception,),
    max_retries=NIGHTLY_RETRIES,
    retry_backoff=60,
    retry_jitter=False,
)
def backup_nightly() -> dict[str, Any]:
    """The guarded nightly pass."""
    from offsite_backup.guard import run_nightly  # noqa: PLC0415 - imported when the task runs

    summary = run_nightly()
    if summary is None:
        return {'skipped': True}
    return {'stamp': summary.stamp, 'objects': len(summary.uploaded), 'bytes': summary.bytes}


@shared_task(name='offsite_backup.check')
def backup_check() -> dict[str, Any]:
    """The morning check; raising makes the task fail where the scheduler reports failures."""
    from offsite_backup.check import check_last_night  # noqa: PLC0415 - imported when the task runs

    newest = check_last_night()
    return {'newest': newest.isoformat() if newest else None}


@shared_task(name='offsite_backup.drill')
def backup_drill() -> dict[str, Any]:
    """The restore drill; raising makes the task fail where the scheduler reports failures."""
    from offsite_backup import conf  # noqa: PLC0415 - imported when the task runs
    from offsite_backup.drill import run_drill  # noqa: PLC0415

    if not conf.enabled():
        return {'skipped': True}
    report = run_drill()
    return {'stamp': report.stamp, 'databases': len(report.databases), 'archives': len(report.archives)}


def beat_schedule(
    *,
    nightly: tuple[int, int] = (3, 0),
    check: tuple[int, int] = (9, 0),
    drill: tuple[int, int, int] | None = None,
) -> dict[str, Any]:
    """Beat entries, in the beat's timezone (UTC recommended): ``(hour, minute)``; the drill ``(day, hour, minute)``."""
    entries = {
        'offsite-backup-nightly': {
            'task': 'offsite_backup.nightly',
            'schedule': crontab(hour=nightly[0], minute=nightly[1]),
        },
        'offsite-backup-check': {
            'task': 'offsite_backup.check',
            'schedule': crontab(hour=check[0], minute=check[1]),
        },
    }
    if drill is not None:
        entries['offsite-backup-drill'] = {
            'task': 'offsite_backup.drill',
            'schedule': crontab(day_of_month=drill[0], hour=drill[1], minute=drill[2]),
        }
    return entries
