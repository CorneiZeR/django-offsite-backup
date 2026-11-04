"""taskiq tasks. Imported only by a project that uses taskiq.

``nightly, check = register(broker)`` — the tasks carry ``schedule`` labels for
``taskiq``'s ``LabelScheduleSource`` (nightly 03:00 UTC, check 09:00 UTC by default).

The nightly task carries ``retry_on_error`` / ``max_retries`` labels (``retries``, 3 by
default; 0 turns them off), which taskiq's ``SimpleRetryMiddleware`` acts on — the broker
needs that middleware. A failed pass gives the night back to the guard, so the retry
re-enters it. The morning check is not retried: every attempt would say MISSING again.

``register(broker, drill='0 5 1 * *')`` also defines the monthly restore drill
(``offsite_backup.drill``, not retried); without ``drill`` it is not registered at all.
The returned pair stays the nightly task and the check; the drill is
``broker.find_task('offsite_backup.drill')``.
"""

from typing import Any

from asgiref.sync import sync_to_async

__all__ = ('register',)


def register(
    broker: Any,
    *,
    nightly: str = '0 3 * * *',
    check: str = '0 9 * * *',
    retries: int = 3,
    drill: str | None = None,
) -> tuple[Any, Any]:
    """Define the nightly pass and the check on ``broker`` with cron schedules (UTC) — and the drill, when given."""
    retry_labels: dict[str, Any] = {'retry_on_error': True, 'max_retries': retries} if retries > 0 else {}

    @broker.task(task_name='offsite_backup.nightly', schedule=[{'cron': nightly, 'cron_offset': None}], **retry_labels)
    async def backup_nightly() -> dict[str, Any]:
        """The guarded nightly pass, in a thread: it is a blocking batch job."""
        from offsite_backup.guard import run_nightly  # noqa: PLC0415 - imported when the task runs

        summary = await sync_to_async(run_nightly, thread_sensitive=False)()
        if summary is None:
            return {'skipped': True}
        return {'stamp': summary.stamp, 'objects': len(summary.uploaded), 'bytes': summary.bytes}

    @broker.task(task_name='offsite_backup.check', schedule=[{'cron': check, 'cron_offset': None}])
    async def backup_check() -> dict[str, Any]:
        """The morning check."""
        from offsite_backup.check import check_last_night  # noqa: PLC0415 - imported when the task runs

        newest = await sync_to_async(check_last_night, thread_sensitive=False)()
        return {'newest': newest.isoformat() if newest else None}

    if drill is not None:

        @broker.task(task_name='offsite_backup.drill', schedule=[{'cron': drill, 'cron_offset': None}])
        async def backup_drill() -> dict[str, Any]:
            """The restore drill, in a thread."""
            from offsite_backup import conf  # noqa: PLC0415 - imported when the task runs
            from offsite_backup.drill import run_drill  # noqa: PLC0415

            if not conf.enabled():
                return {'skipped': True}
            report = await sync_to_async(run_drill, thread_sensitive=False)()
            return {'stamp': report.stamp, 'databases': len(report.databases), 'archives': len(report.archives)}

    return backup_nightly, backup_check
