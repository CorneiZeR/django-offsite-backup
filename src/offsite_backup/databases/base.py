"""Database sources: one dump per target, piped through compression and encryption."""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from django.utils.module_loading import import_string

from offsite_backup.backend import Backend
from offsite_backup.exceptions import MisconfiguredError
from offsite_backup.sealing import Sealing

__all__ = ('DatabaseSource', 'database_settings')


def database_settings(alias: str) -> Mapping[str, Any]:
    """The connection settings of ``alias`` — from ``connections``, so aliases added at run time count."""
    from django.db import connections  # noqa: PLC0415 - only when a database is touched

    try:
        return connections.databases[alias]
    except KeyError as error:
        msg = f'DATABASES has no alias {alias!r}'
        raise MisconfiguredError(msg) from error


class DatabaseSource(Backend):
    """Dumps databases. ``targets()`` may be computed at run time."""

    def targets(self) -> list[str]:
        """``ALIASES`` (default ``['default']``), computed when it is a callable's dotted path."""
        aliases = self.option('ALIASES', ['default'])
        if isinstance(aliases, str):
            found: Callable[[], Any] = import_string(aliases)
            aliases = found()
        return [str(alias).strip() for alias in aliases if str(alias).strip()]

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
