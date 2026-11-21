"""Whole-night rotation, and the depth line that follows it.

Counted in calendar nights (UTC) read from the object *name* — not keys (a night's media
is several volumes) and not stamps (several runs a day would push out nights whose lock
still holds). Only nights that hold a whole backup count, in three tiers: the newest
``KEEP_NIGHTS``; then, among older nights, one per ISO week for the newest ``KEEP_WEEKS``
weeks; then, older still, one per calendar month for the newest ``KEEP_MONTHS`` months. A
week's or a month's night is the *earliest* one present, so it does not move while the week
or month ages. A pass that left the media out is kept while newer than the oldest daily
night kept, and goes with it. Every version of an expired key is deleted, delete markers
included. A version still locked is left for a later night: a refused delete must not fail
the night over an object that is safe.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from offsite_backup import naming
from offsite_backup.stores.base import Store

__all__ = ('Depth', 'Keep', 'Rotated', 'depth', 'rotate')

logger = logging.getLogger('offsite_backup')


@dataclass(frozen=True)
class Keep:
    """How many whole nights each tier keeps: the newest ``nights``, then ``weeks`` weekly and ``months`` monthly."""

    nights: int
    weeks: int = 0
    months: int = 0


@dataclass
class Rotated:
    """What one rotation removed, and what it had to leave for a later night."""

    removed: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Depth:
    """How far back a kind's backups reach: nights with a downloadable backup, and the oldest."""

    nights: int
    since: date


def _week(night: date) -> tuple[int, int]:
    year, week, _ = night.isocalendar()
    return year, week


def _month(night: date) -> tuple[int, int]:
    return night.year, night.month


def _representatives(nights: Iterable[date], count: int, group: Callable[[date], tuple[int, int]]) -> set[date]:
    """The earliest night of each of the newest ``count`` groups."""
    earliest: dict[tuple[int, int], date] = {}
    for night in nights:
        key = group(night)
        earliest[key] = min(night, earliest.get(key, night))
    return {earliest[key] for key in sorted(earliest, reverse=True)[:count]}


def _kept(whole: set[date], keep: Keep) -> tuple[set[date], date]:
    """The whole nights kept, and the oldest daily one: what partial passes are measured against."""
    daily = sorted(whole, reverse=True)[: keep.nights]
    oldest = min(daily)
    weekly = _representatives((night for night in whole if night < oldest), keep.weeks, _week)
    floor = min(weekly, default=oldest)
    monthly = _representatives((night for night in whole if night < floor), keep.months, _month)
    return set(daily) | weekly | monthly, oldest


def _expired(keys: list[str], keep: Keep) -> set[str]:
    nights: dict[str, date] = {}
    for key in keys:
        night = naming.night(key)
        if night is not None:
            nights[key] = night
    whole = {key: night for key, night in nights.items() if not naming.is_partial(key)}
    if whole:
        kept, oldest = _kept(set(whole.values()), keep)
        expired = {key for key, night in whole.items() if night not in kept}
        expired |= {key for key, night in nights.items() if key not in whole and night < oldest}
        return expired
    # nothing whole yet (a fresh setup that has only seen deploys): partial nights count
    # by themselves, so the prefix cannot grow without bound
    kept = set(sorted(set(nights.values()), reverse=True)[: keep.nights])
    return {key for key, night in nights.items() if night not in kept}


def rotate(store: Store, folder: str, keep: int | Keep, *, now: datetime | None = None) -> Rotated:
    """Delete every version of every backup in ``folder`` that no tier of ``keep`` keeps; an int is nights only."""
    if not folder.endswith('/'):
        msg = f'rotation is scoped to a folder ending in "/", got {folder!r}'
        raise ValueError(msg)
    moment = now or datetime.now(timezone.utc)
    tiers = keep if isinstance(keep, Keep) else Keep(keep)
    stored = store.versions(folder)
    result = Rotated()
    for key in sorted(_expired(list(stored), tiers)):
        held = False
        for version in stored[key]:
            until = store.locked_until(key, version)
            if until is not None and until > moment:
                logger.warning(
                    'Backup still locked; left for a later night',
                    extra={'ob_key': key, 'ob_until': until.isoformat()},
                )
                held = True
                continue
            store.delete(key, version)
        (result.held if held else result.removed).append(key)
    return result


def depth(store: Store, folder: str) -> Depth | None:
    """Nights under ``folder`` whose current version is an object, and the oldest. Best effort.

    A depth that cannot be read is a line left out of the message, not a failed backup.
    """
    try:
        keys = store.current(folder)
    except Exception:  # noqa: BLE001 - see the docstring
        logger.warning('Backup depth not read', extra={'ob_folder': folder})
        return None
    nights = {night for night in map(naming.night, keys) if night is not None}
    if not nights:
        return None
    return Depth(nights=len(nights), since=min(nights))
