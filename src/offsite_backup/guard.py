"""The nightly pass, at most once per UTC night.

A deploy that recreates the scheduler around the nightly time can fire the task twice
(the old scheduler as it stops, the new one as it starts). So the night is claimed in a
cache every worker shares: a night already done, or being done, is skipped; a failed pass
gives the night back to its retry; a pass whose worker died holds the night for
``RUNNING_SECONDS`` at most; "done" is written before "running" is released. Manual and
deploy passes are not guarded.
"""

import logging
from datetime import datetime, timezone

from offsite_backup import conf
from offsite_backup.outcome import Summary
from offsite_backup.pass_ import run

__all__ = ('night_key', 'run_nightly')

logger = logging.getLogger('offsite_backup')


def night_key(settings: conf.Settings, now: datetime) -> str:
    """The cache key of one prefix's night."""
    return f'offsite_backup:{settings.prefix}:{now.astimezone(timezone.utc):%Y-%m-%d}'


def run_nightly(*, now: datetime | None = None) -> Summary | None:
    """The nightly pass, guarded; ``None`` when the night is done or taken (or backups are off)."""
    settings = conf.read()
    if not conf.enabled(settings):
        logger.info('Nightly backup skipped: not configured on this machine')
        return None
    guard = settings['GUARD']
    if not guard.get('ENABLED', True):
        return run(reason='nightly', now=now)
    from django.core.cache import caches  # noqa: PLC0415 - only when the guard runs

    cache = caches[guard.get('CACHE', 'default')]
    moment = now or datetime.now(timezone.utc)
    night = night_key(settings, moment)
    if not cache.add(f'{night}:running', 1, timeout=int(guard.get('RUNNING_SECONDS', 3600))):
        logger.info('Nightly backup skipped: tonight is being written')
        return None
    if cache.get(f'{night}:done'):
        # checked under the claim, so a pass finishing in between cannot be missed
        cache.delete(f'{night}:running')
        logger.info('Nightly backup skipped: tonight is already written')
        return None
    try:
        summary = run(reason='nightly', now=now)
    except BaseException:
        cache.delete(f'{night}:running')
        raise
    cache.set(f'{night}:done', 1, timeout=int(guard.get('DONE_SECONDS', 26 * 3600)))
    cache.delete(f'{night}:running')
    return summary
