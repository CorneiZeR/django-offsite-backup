"""Celery tasks and a beat schedule. Imported only by a project that uses Celery.

``CELERY_BEAT_SCHEDULE = {**beat_schedule(), ...}`` — nightly 03:00 UTC, check 09:00 UTC.
"""

from typing import Any

from celery import shared_task
from celery.schedules import crontab

__all__ = ('backup_check', 'backup_nightly', 'beat_schedule')


@shared_task(name='offsite_backup.nightly')
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


def beat_schedule(*, nightly: tuple[int, int] = (3, 0), check: tuple[int, int] = (9, 0)) -> dict[str, Any]:
    """Beat entries for both tasks; ``(hour, minute)`` in the beat's timezone (UTC recommended)."""
    return {
        'offsite-backup-nightly': {
            'task': 'offsite_backup.nightly',
            'schedule': crontab(hour=nightly[0], minute=nightly[1]),
        },
        'offsite-backup-check': {
            'task': 'offsite_backup.check',
            'schedule': crontab(hour=check[0], minute=check[1]),
        },
    }
