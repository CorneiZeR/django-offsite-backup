"""Database sources: one dump per target, piped through compression and encryption."""

from pathlib import Path

from offsite_backup.backend import Backend
from offsite_backup.sealing import Sealing

__all__ = ('DatabaseSource',)


class DatabaseSource(Backend):
    """Dumps databases. ``targets()`` may be computed at run time."""

    def targets(self) -> list[str]:
        """The database aliases this run dumps."""
        raise NotImplementedError

    def suffix(self) -> str:
        """The object suffix, e.g. ``sql.zst.age``."""
        raise NotImplementedError

    def dump(self, alias: str, into: Path, sealing: Sealing) -> Path:
        """Dump ``alias`` sealed into the file ``into``; return it."""
        raise NotImplementedError

    def verify(self, plain: Path) -> None:
        """Raise unless the decrypted, decompressed dump at ``plain`` is complete."""
        raise NotImplementedError

    def restore(self, plain: Path, alias: str, *, clean: bool = False) -> None:
        """Load the decrypted, decompressed dump at ``plain`` into ``alias``."""
        raise NotImplementedError
