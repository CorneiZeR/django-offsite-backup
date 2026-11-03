"""The nightly pass, at most once per UTC night.

A deploy that recreates the scheduler around the nightly time can fire the task twice
(the old scheduler as it stops, the new one as it starts). So the night is claimed where
every worker sees it: a night already done, or being done, is skipped; a failed pass gives
the night back to its retry; a pass whose worker died does not hold the night for ever.
Manual and deploy passes are not guarded.

Two backends (``GUARD['BACKEND']``):

* ``cache`` (default) — a shared Django cache: ``running`` taken with ``add`` and a TTL of
  ``RUNNING_SECONDS``, ``done`` written (``DONE_SECONDS``) before ``running`` is released.
* ``database`` — no cache needed: ``running`` is a PostgreSQL session advisory lock on
  ``GUARD['DATABASE']``, released by the server the moment a dead worker's connection
  closes; ``done`` is asked of the store itself — a whole backup stamped with tonight's date.
"""

import contextlib
import hashlib
import logging
from collections.abc import Iterator
from datetime import datetime, timezone

from offsite_backup import conf, naming
from offsite_backup.outcome import Summary
from offsite_backup.pass_ import components, run

__all__ = ('BACKENDS', 'night_key', 'run_nightly')

logger = logging.getLogger('offsite_backup')

BACKENDS = ('cache', 'database')


def night_key(settings: conf.Settings, now: datetime) -> str:
    """The cache key of one prefix's night."""
    return f'offsite_backup:{settings.prefix}:{now.astimezone(timezone.utc):%Y-%m-%d}'


def _cache_guarded(settings: conf.Settings, moment: datetime, now: datetime | None) -> Summary | None:
    from django.core.cache import caches  # noqa: PLC0415 - only when the guard runs

    guard = settings['GUARD']
    cache = caches[guard.get('CACHE', 'default')]
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


def lock_id(prefix: str) -> int:
    """A stable signed 64-bit advisory-lock key for one prefix."""
    return int.from_bytes(hashlib.sha256(f'offsite_backup:{prefix}'.encode()).digest()[:8], 'big', signed=True)


@contextlib.contextmanager
def _advisory_lock(alias: str, key: int) -> Iterator[bool]:
    """Hold ``pg_try_advisory_lock(key)`` for the block; yields whether it was taken."""
    from django.db import connections  # noqa: PLC0415 - only when the guard runs

    with connections[alias].cursor() as cursor:
        cursor.execute('SELECT pg_try_advisory_lock(%s)', [key])
        taken = bool(cursor.fetchone()[0])
    try:
        yield taken
    finally:
        if taken:
            with connections[alias].cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(%s)', [key])


def _done_tonight(settings: conf.Settings, moment: datetime) -> bool:
    """Whether every part tonight's pass writes already holds a whole backup stamped with this UTC date.

    Every database's folder, not only the config: a pass where one database failed still
    uploads the rest and the config, and its retry must not be taken for a done night.
    """
    from offsite_backup.check import newest_whole  # noqa: PLC0415 - avoids a cycle

    parts = components(settings)
    kinds = [naming.CONFIG] if parts.config is not None else []
    if parts.databases is not None:
        kinds += [naming.db_kind(alias) for alias in parts.databases.targets()]
    if not kinds:
        kinds = [naming.media_kind(source.name) for source in parts.media]
    tonight = moment.astimezone(timezone.utc).date()
    for kind in kinds:
        newest = newest_whole(parts.store, naming.folder(settings.prefix, kind))
        if newest is None or newest.date() != tonight:
            return False
    return True


def _database_guarded(settings: conf.Settings, moment: datetime, now: datetime | None) -> Summary | None:
    alias = str(settings['GUARD'].get('DATABASE', 'default'))
    with _advisory_lock(alias, lock_id(settings.prefix)) as taken:
        if not taken:
            logger.info('Nightly backup skipped: tonight is being written')
            return None
        if _done_tonight(settings, moment):
            logger.info('Nightly backup skipped: tonight is already written')
            return None
        return run(reason='nightly', now=now)


def run_nightly(*, now: datetime | None = None) -> Summary | None:
    """The nightly pass, guarded; ``None`` when the night is done or taken (or backups are off)."""
    settings = conf.read()
    if not conf.enabled(settings):
        logger.info('Nightly backup skipped: not configured on this machine')
        return None
    guard = settings['GUARD']
    if not guard.get('ENABLED', True):
        return run(reason='nightly', now=now)
    moment = now or datetime.now(timezone.utc)
    if guard.get('BACKEND', 'cache') == 'database':
        return _database_guarded(conf.load(), moment, now)
    return _cache_guarded(settings, moment, now)
