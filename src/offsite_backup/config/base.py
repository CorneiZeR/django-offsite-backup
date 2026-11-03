"""Configuration sources: what it takes to rebuild the machine, archived last."""

from pathlib import Path

from offsite_backup.backend import Backend
from offsite_backup.sealing import Sealing

__all__ = ('ConfigSource',)


class ConfigSource(Backend):
    """Writes one archive. A config object for a stamp means every part before it went up."""

    def suffix(self) -> str:
        """The object suffix."""
        return 'tar.zst.age'

    def archive(self, into: Path, sealing: Sealing) -> Path:
        """Write the sealed archive into ``into``; return it."""
        raise NotImplementedError
