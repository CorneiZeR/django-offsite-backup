"""Stores: where backups are kept, and the versions, locks and deletes rotation needs.

``STORE`` may be a list: every pass is written to each store, each one's lock read back on
its own, each rotated by its own ``KEEP_NIGHTS``. Reads (restore, list, the morning check's
"tonight is done") go to the first store unless one is named.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from offsite_backup import findings as checks
from offsite_backup.backend import Backend

__all__ = ('Store', 'Version', 'Wrote')


@dataclass(frozen=True)
class Version:
    """One stored version of a key. A delete marker holds no bytes and no lock."""

    id: str
    marker: bool = False
    latest: bool = False
    size: int = 0


@dataclass(frozen=True)
class Wrote:
    """One object, as it went up."""

    key: str
    size: int
    locked_until: datetime | None = None
    #: the ``NAME`` of the store it went to; blank for a single unnamed store
    store: str = ''


class Store(Backend):
    """Where backups are kept. Every method is synchronous; payload travels through files."""

    #: whether objects are immutable once written (Object Lock)
    LOCKS = False

    @property
    def name(self) -> str:
        """``NAME``: how messages, ``--store`` and the checks call this store among several."""
        return str(self.option('NAME', '')).strip()

    def keep_nights(self, default: int) -> int:
        """``KEEP_NIGHTS`` on this store, else ``RETENTION['KEEP_NIGHTS']``: each store rotates on its own."""
        return int(self.option('KEEP_NIGHTS', default))

    def check(self) -> list[checks.CheckMessage]:
        """A store's own ``KEEP_NIGHTS``, when given, is a positive integer."""
        found = super().check()
        keep = self.option('KEEP_NIGHTS')
        if keep is not None and (not isinstance(keep, int) or isinstance(keep, bool) or keep < 1):
            found.append(
                checks.Error(
                    f"{self.label}['KEEP_NIGHTS'] is {keep!r}; expected a positive integer.",
                    id='offsite_backup.E001',
                )
            )
        return found

    def put(self, path: Path, key: str, *, now: datetime | None = None) -> Wrote:
        """Store ``path`` under ``key`` (locked, where the store locks) and confirm it landed."""
        raise NotImplementedError

    def copy(self, source: str, key: str, *, now: datetime | None = None) -> Wrote:
        """Copy one stored object to another key, locked like a fresh upload."""
        raise NotImplementedError

    def versions(self, prefix: str) -> dict[str, list[Version]]:
        """Every version under ``prefix``, delete markers included, by key."""
        raise NotImplementedError

    def delete(self, key: str, version: Version) -> None:
        """Remove one version for good."""
        raise NotImplementedError

    def locked_until(self, key: str, version: Version) -> datetime | None:
        """When a version's lock ends, or ``None`` for no lock."""
        return None

    def get(self, key: str, into: Path) -> None:
        """Download the current version of ``key`` into the file ``into``."""
        raise NotImplementedError

    def current(self, prefix: str) -> list[str]:
        """Keys under ``prefix`` whose current version is an object, not a delete marker."""
        return sorted(
            key
            for key, held in self.versions(prefix).items()
            if any(version.latest and not version.marker for version in held)
        )
