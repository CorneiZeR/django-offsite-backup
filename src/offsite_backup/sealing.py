"""How a plain stream becomes a sealed object: compress, THEN encrypt.

An encrypted stream compresses by nothing, so the order is not a preference.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from offsite_backup.encryption.base import Sealer

__all__ = ('Sealing',)


@dataclass(frozen=True)
class Sealing:
    """The stages that follow a source's own output."""

    sealer: Sealer
    compress: Sequence[str] = ()
    extra_recipients: Sequence[str] = field(default_factory=tuple)

    def stages(self, *, compress: bool = True) -> list[list[str]]:
        """``[compress], seal`` — or seal alone for bytes already compressed (media)."""
        stages = [list(self.compress)] if compress and self.compress else []
        return [*stages, self.sealer.pipeline(self.extra_recipients)]
