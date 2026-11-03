"""Object names: ``<PREFIX>/<kind>/<stamp>[.<marker>][.<NNN>].<suffix>``.

The stamp (``2026-09-12T03-00Z``) sorts as text and is a filename everywhere. The night
a backup belongs to is read from the name, never from the store's timestamps: several
runs on one UTC date are one night. A key whose name has no stamp is not a backup and is
never touched by rotation.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

__all__ = (
    'CONFIG',
    'RELEASED',
    'STAMP_FORMAT',
    'WITHOUT_MEDIA',
    'Name',
    'db_kind',
    'folder',
    'is_partial',
    'join',
    'media_kind',
    'night',
    'object_name',
    'parse',
    'stamp',
)

STAMP_FORMAT = '%Y-%m-%dT%H-%MZ'
#: marks every object of a pass that left the media out; never counted as a night
WITHOUT_MEDIA = 'without-media'
CONFIG = 'config'
#: a sibling of the kinds that no rotation ever reads (``keep_apart``)
RELEASED = 'released'

_STAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}-\d{2}Z')
_NUMBER = re.compile(r'\d{3,}')


@dataclass(frozen=True)
class Name:
    """A backup object's name, taken apart."""

    stamp: str
    when: datetime
    marker: str
    number: int | None
    suffix: str

    @property
    def night(self) -> date:
        """The UTC date the object belongs to."""
        return self.when.date()


def stamp(now: datetime | None = None) -> str:
    """``2026-09-12T03-00Z`` for ``now`` (UTC)."""
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.strftime(STAMP_FORMAT)


def parse(key: str) -> Name | None:
    """Take a key's last segment apart, or ``None`` when it carries no stamp."""
    parts = key.rsplit('/', 1)[-1].split('.')
    if not _STAMP.fullmatch(parts[0]):
        return None
    when = datetime.strptime(parts[0], STAMP_FORMAT).replace(tzinfo=timezone.utc)
    rest = parts[1:]
    marker = ''
    if rest and rest[0] == WITHOUT_MEDIA:
        marker, rest = rest[0], rest[1:]
    number = None
    if rest and _NUMBER.fullmatch(rest[0]):
        number, rest = int(rest[0]), rest[1:]
    return Name(stamp=parts[0], when=when, marker=marker, number=number, suffix='.'.join(rest))


def night(key: str) -> date | None:
    """The UTC night a key belongs to, or ``None`` for a key that is not a backup."""
    name = parse(key)
    return name.night if name else None


def is_partial(key: str) -> bool:
    """Whether a key was written by a pass that left the media out."""
    name = parse(key)
    return bool(name and name.marker == WITHOUT_MEDIA)


def object_name(at: str, suffix: str, *, partial: bool = False, number: int | None = None) -> str:
    """The last segment of a key."""
    parts = [at]
    if partial:
        parts.append(WITHOUT_MEDIA)
    if number is not None:
        parts.append(f'{number:03d}')
    parts.append(suffix)
    return '.'.join(parts)


def db_kind(alias: str) -> str:
    """``db`` for the default database, ``db-<alias>`` — a sibling, not a child — for others."""
    return 'db' if alias == 'default' else f'db-{alias}'


def media_kind(name: str) -> str:
    """``media`` for an unnamed source, ``media-<name>`` for others."""
    return f'media-{name}' if name else 'media'


def folder(prefix: str, kind: str) -> str:
    """``<prefix>/<kind>/`` — the trailing slash keeps ``db/`` from matching ``db-old/``."""
    return f'{prefix}/{kind}/'


def join(prefix: str, kind: str, name: str) -> str:
    """A full key."""
    return folder(prefix, kind) + name
