"""What a pass or a check produced: the record messages are written from."""

from dataclasses import dataclass, field
from datetime import datetime

from offsite_backup.rotation import Depth
from offsite_backup.stores.base import Wrote

__all__ = ('FAILED', 'MISSING', 'UNCHECKED', 'WRITTEN', 'Outcome', 'Summary')

WRITTEN = 'written'
FAILED = 'failed'
MISSING = 'missing'
UNCHECKED = 'unchecked'


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

    @property
    def bytes(self) -> int:
        """Bytes sent."""
        return sum(wrote.size for wrote in self.uploaded)

    @property
    def keys(self) -> list[str]:
        """Keys written."""
        return [wrote.key for wrote in self.uploaded]


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
