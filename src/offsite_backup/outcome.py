"""What a pass, a check or a drill produced: the record messages are written from."""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

from offsite_backup.rotation import Depth
from offsite_backup.stores.base import Wrote

__all__ = (
    'DRILLED',
    'DRILL_FAILED',
    'FAILED',
    'MISSING',
    'UNCHECKED',
    'WRITTEN',
    'Drill',
    'DrilledArchive',
    'DrilledDatabase',
    'Outcome',
    'Summary',
)

WRITTEN = 'written'
FAILED = 'failed'
MISSING = 'missing'
UNCHECKED = 'unchecked'
DRILLED = 'drilled'
DRILL_FAILED = 'drill_failed'


@dataclass
class Summary:
    """One pass, as it went."""

    stamp: str
    prefix: str
    reason: str
    media: bool
    uploaded: list[Wrote] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0
    depth: Depth | None = None
    #: every configured store's ``NAME``, in order; ``uploaded`` holds one write per store
    stores: list[str] = field(default_factory=list)
    #: a store that refused an object: written to no more, not rotated, the pass failed by its name
    failed_stores: dict[str, str] = field(default_factory=dict)

    @property
    def _first(self) -> dict[str, Wrote]:
        found: dict[str, Wrote] = {}
        for wrote in self.uploaded:
            found.setdefault(wrote.key, wrote)
        return found

    @property
    def bytes(self) -> int:
        """Bytes of the backup — once, however many stores hold a copy."""
        return sum(wrote.size for wrote in self._first.values())

    @property
    def keys(self) -> list[str]:
        """Keys written, each once."""
        return list(self._first)

    @property
    def objects(self) -> int:
        """Objects in the backup, each counted once."""
        return len(self._first)


@dataclass
class DrilledDatabase:
    """One database restored into the throwaway one and counted there — and live, when compared."""

    alias: str
    key: str
    tables: int = 0
    rows: int = 0
    live_tables: int | None = None
    live_rows: int | None = None
    #: tables the live database has and the restored one does not
    missing: list[str] = field(default_factory=list)


@dataclass
class DrilledArchive:
    """A media volume or the configuration archive, decrypted and listed."""

    kind: str
    key: str
    files: int = 0
    bytes: int = 0


@dataclass
class Drill:
    """One restore drill, as far as it got."""

    stamp: str
    prefix: str
    target: str
    databases: list[DrilledDatabase] = field(default_factory=list)
    archives: list[DrilledArchive] = field(default_factory=list)
    #: what did not restore, open or count: its name -> the error
    failed: dict[str, str] = field(default_factory=dict)
    seconds: float = 0.0


def _plain(value: Any) -> Any:
    """``asdict`` output made JSON-safe: times as ISO text."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class Outcome:
    """What happened, where, and why it ran — everything a formatter may say."""

    kind: str
    prefix: str
    env: str = ''
    name: str = ''
    reason: str = ''
    summary: Summary | None = None
    error: str = ''
    newest: datetime | None = None
    stale_hours: float = 0.0
    now: datetime | None = None
    drill: Drill | None = None

    def as_dict(self) -> dict[str, Any]:
        """The outcome as JSON-ready data — what a webhook receives beside the text."""
        found: dict[str, Any] = _plain(asdict(self))
        if self.summary is not None:
            found['summary']['bytes'] = self.summary.bytes
            found['summary']['keys'] = self.summary.keys
        return found
