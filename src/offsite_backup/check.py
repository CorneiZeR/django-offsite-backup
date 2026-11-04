"""The morning check: notice the night that never ran.

It asks the store for the newest *whole* backup — objects of a pass without media do not
count, so a deploy at noon cannot hide a night that never ran — under the config folder
and under every database's folder. Older than ``STALE_HOURS`` or absent: MISSING, said
and raised (so the scheduler's failure mail fires too). Store unreadable: UNCHECKED, as
loud. Media written tonight does not stand in for a database that was not. With several
stores every one is asked, and a folder is named with its store (``offsite: db/``): the
copy that stopped arriving is the one nobody looks at until it is needed.
"""

import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from offsite_backup import conf, naming
from offsite_backup.exceptions import BackupMissingError, BackupUncheckedError
from offsite_backup.notify import notify
from offsite_backup.outcome import MISSING, UNCHECKED, Outcome
from offsite_backup.stores.base import Store

__all__ = ('check_last_night', 'newest_whole')

logger = logging.getLogger('offsite_backup')


def newest_whole(store: Store, folder: str) -> datetime | None:
    """When the newest whole backup under ``folder`` was written, or ``None``."""
    found = [naming.parse(key) for key in store.current(folder)]
    stamps = [name.when for name in found if name is not None and name.marker != naming.WITHOUT_MEDIA]
    return max(stamps, default=None)


def check_last_night(*, now: datetime | None = None) -> datetime | None:
    """Raise unless every checked folder has a whole backup within ``STALE_HOURS``; answer the oldest newest.

    ``None`` when backups are off on this machine.
    """
    from offsite_backup.pass_ import components  # noqa: PLC0415 - builds backends

    settings = conf.read()
    if not conf.enabled(settings):
        return None
    moment = now or datetime.now(timezone.utc)
    base = Outcome(
        kind=MISSING, prefix=settings.prefix, env=str(settings['ENV']), name=str(settings['NAME']), now=moment
    )
    try:
        parts = components(conf.load())
        kinds = [naming.CONFIG] if parts.config is not None else []
        if parts.databases is not None:
            kinds += [naming.db_kind(alias) for alias in parts.databases.targets()]
        if not kinds:
            kinds = [naming.media_kind(source.name) for source in parts.media]
        several = len(parts.stores) > 1
        newest = {
            (f'{store.name}: ' if several else '') + kind: newest_whole(store, naming.folder(settings.prefix, kind))
            for store in parts.stores
            for kind in kinds
        }
    except Exception as error:
        text = f'{type(error).__name__}: {error}'
        notify(replace(base, kind=UNCHECKED, error=text), settings)
        raise BackupUncheckedError(text) from error
    limit = timedelta(hours=settings.stale_hours)
    # a database with no backup at all appeared after the last pass (a new tenant): not yet
    # backed up, which is not MISSING. One whose dumps always fail is named by every FAILED
    # message of the nightly pass itself, so it is not silent either
    new: list[str] = []
    for store in parts.stores:
        # per store: a store that never received anything must not hide behind another's backups
        mine = (f'{store.name}: ' if several else '') + 'db'
        databases = {kind: when for kind, when in newest.items() if kind.startswith(mine)}
        # only beside a database that is backed up: one with nothing at all and nothing to compare is MISSING
        if any(when is not None for when in databases.values()):
            new += sorted(kind for kind, when in databases.items() if when is None)
    if new:
        logger.info('Databases not backed up yet', extra={'ob_kinds': new})
    stale = {kind: when for kind, when in newest.items() if kind not in new and (when is None or moment - when > limit)}
    if stale:
        oldest = min(stale.values(), key=lambda when: when or datetime.min.replace(tzinfo=timezone.utc))
        notify(replace(base, kind=MISSING, newest=oldest, stale_hours=settings.stale_hours), settings)
        named = ', '.join(
            f'{kind}/ ({when:%Y-%m-%d %H:%M} UTC)' if when else f'{kind}/ (none)' for kind, when in stale.items()
        )
        msg = f'No whole backup within {settings.stale_hours:g} h under {settings.prefix}/: {named}'
        raise BackupMissingError(msg)
    return min((when for when in newest.values() if when is not None), default=None)
