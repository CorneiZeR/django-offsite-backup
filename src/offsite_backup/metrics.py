"""The last success per kind, for whatever watches the machine: a cron job, a healthcheck, Prometheus.

The morning check says MISSING once a day, in the chat. A monitor wants the same answer
whenever it asks, silently, and as a number: how old is the newest whole backup of each
kind — the config, every database, every media source. ``freshness()`` reads it from the
store, as the morning check does (objects of a pass without media do not count), and
``prometheus()`` writes it in the text exposition format, for a node-exporter textfile
collector or any scraper that reads a file. Nothing here notifies.
"""

import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from offsite_backup import conf, naming
from offsite_backup.check import newest_whole
from offsite_backup.exceptions import BackupUncheckedError

__all__ = (
    'FRESH',
    'STALE',
    'UNREADABLE',
    'Age',
    'Freshness',
    'as_json',
    'freshness',
    'lines',
    'prometheus',
    'status',
    'write_textfile',
)


#: what ``status`` answers, and the command lines exit with
FRESH, STALE, UNREADABLE = 0, 1, 2


@dataclass(frozen=True)
class Age:
    """One kind's newest whole backup and how old it is."""

    kind: str
    newest: datetime | None
    seconds: float | None
    stale: bool


@dataclass(frozen=True)
class Freshness:
    """Every kind's age, as of ``checked``; ``up`` is false when the store could not be read."""

    prefix: str
    checked: datetime
    stale_hours: float
    up: bool = True
    kinds: list[Age] = field(default_factory=list)
    error: str = ''

    @property
    def stale(self) -> list[Age]:
        """The kinds with no whole backup within ``stale_hours``."""
        return [age for age in self.kinds if age.stale]


def freshness(*, now: datetime | None = None, stale_hours: float | None = None) -> Freshness:
    """Each kind's newest whole backup and its age; raises ``BackupUncheckedError`` when the store cannot be read."""
    from offsite_backup.pass_ import components  # noqa: PLC0415 - builds backends

    settings = conf.load()
    moment = now or datetime.now(timezone.utc)
    limit = settings.stale_hours if stale_hours is None else float(stale_hours)
    try:
        parts = components(settings)
        kinds = [naming.CONFIG] if parts.config is not None else []
        if parts.databases is not None:
            kinds += [naming.db_kind(alias) for alias in parts.databases.targets()]
        kinds += [naming.media_kind(source.name) for source in parts.media]
        newest = {kind: newest_whole(parts.store, naming.folder(settings.prefix, kind)) for kind in kinds}
    except Exception as error:
        msg = f'{type(error).__name__}: {error}'
        raise BackupUncheckedError(msg) from error
    ages = []
    for kind, when in newest.items():
        seconds = None if when is None else (moment - when).total_seconds()
        ages.append(Age(kind=kind, newest=when, seconds=seconds, stale=seconds is None or seconds > limit * 3600))
    return Freshness(prefix=settings.prefix, checked=moment, stale_hours=limit, kinds=ages)


def lines(found: Freshness) -> list[str]:
    """A kind per line: the newest whole backup and its age, ``STALE`` where it is too old or absent."""
    shown = []
    for age in found.kinds:
        if age.newest is None or age.seconds is None:
            when = 'none'
        else:
            when = f'{age.newest:%Y-%m-%d %H:%M} UTC, {age.seconds / 3600:.1f} h ago'
        shown.append(f'  {age.kind}/: {when}' + ('  STALE' if age.stale else ''))
    return [f'{found.prefix}/ (stale after {found.stale_hours:g} h)', *shown]


def as_json(found: Freshness) -> dict[str, object]:
    """The same, for a script."""
    return {
        'prefix': found.prefix,
        'checked': found.checked.isoformat(),
        'stale_hours': found.stale_hours,
        'up': found.up,
        'error': found.error,
        'kinds': [
            {
                'kind': age.kind,
                'newest': age.newest.isoformat() if age.newest else None,
                'age_seconds': age.seconds,
                'stale': age.stale,
            }
            for age in found.kinds
        ],
    }


def _label(value: str) -> str:
    """A label value escaped as the exposition format wants it."""
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n')


def prometheus(found: Freshness) -> str:
    """The text exposition format: per kind the last success, its age and whether it is stale; and ``up``."""
    prefix = _label(found.prefix)
    lines = [
        '# HELP offsite_backup_up Whether the store could be read.',
        '# TYPE offsite_backup_up gauge',
        f'offsite_backup_up{{prefix="{prefix}"}} {int(found.up)}',
        '# HELP offsite_backup_checked_timestamp_seconds When the store was asked.',
        '# TYPE offsite_backup_checked_timestamp_seconds gauge',
        f'offsite_backup_checked_timestamp_seconds{{prefix="{prefix}"}} {found.checked.timestamp():.0f}',
    ]
    if found.up:
        lines += [
            '# HELP offsite_backup_last_success_timestamp_seconds When the newest whole backup of a kind was written.',
            '# TYPE offsite_backup_last_success_timestamp_seconds gauge',
            *(
                f'offsite_backup_last_success_timestamp_seconds{{prefix="{prefix}",kind="{_label(age.kind)}"}} '
                f'{age.newest.timestamp():.0f}'
                for age in found.kinds
                if age.newest is not None
            ),
            '# HELP offsite_backup_stale Whether a kind has no whole backup within STALE_HOURS.',
            '# TYPE offsite_backup_stale gauge',
            *(
                f'offsite_backup_stale{{prefix="{prefix}",kind="{_label(age.kind)}"}} {int(age.stale)}'
                for age in found.kinds
            ),
        ]
    return '\n'.join(lines) + '\n'


def write_textfile(path: Path | str, text: str) -> None:
    """Replace ``path`` atomically: a collector never reads half a file."""
    target = Path(path)
    handle, partial = tempfile.mkstemp(prefix=f'.{target.name}.', dir=target.parent)
    try:
        with os.fdopen(handle, 'w') as stream:
            stream.write(text)
        Path(partial).chmod(0o644)
        Path(partial).replace(target)
    except BaseException:
        Path(partial).unlink(missing_ok=True)
        raise


def status(*, stale_hours: float | None = None, textfile: Path | str | None = None) -> tuple[int, Freshness]:
    """``freshness()`` as an exit code — ``FRESH``, ``STALE`` or ``UNREADABLE`` — writing the textfile when asked."""
    try:
        found = freshness(stale_hours=stale_hours)
        code = STALE if found.stale else FRESH
    except BackupUncheckedError as error:
        settings = conf.read()
        limit = settings.stale_hours if stale_hours is None else float(stale_hours)
        found = Freshness(
            prefix=settings.prefix, checked=datetime.now(timezone.utc), stale_hours=limit, up=False, error=str(error)
        )
        code = UNREADABLE
    if textfile:
        write_textfile(textfile, prometheus(found))
    return code, found
