"""Database sources: one dump per target, piped through compression and encryption.

A target is an alias. Its connection settings come from the source's own ``CONNECTIONS``
block when it has one — a server with no Django project — and from Django's ``DATABASES``
otherwise, read through ``connections`` so aliases added at run time count.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from offsite_backup import conf
from offsite_backup.backend import Backend
from offsite_backup.exceptions import MisconfiguredError
from offsite_backup.registry import import_string
from offsite_backup.sealing import Sealing

__all__ = ('DatabaseSource',)

_LOOPBACK = frozenset({'', 'localhost', '127.0.0.1', '::1'})


class DatabaseSource(Backend):
    """Dumps databases. ``targets()`` may be computed at run time."""

    #: what an empty ``PORT`` means, so ``''`` and the default port are one server
    DEFAULT_PORT: ClassVar[str] = ''

    def targets(self) -> list[str]:
        """``ALIASES`` (default ``['default']``), computed when it is a callable's dotted path."""
        aliases = self.option('ALIASES', ['default'])
        if isinstance(aliases, str):
            aliases = import_string(aliases)()
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

    def tables(self, alias: str) -> dict[str, int]:
        """Every table of ``alias`` with its exact row count — what the restore drill compares."""
        msg = f'{type(self).__name__} cannot count tables, so the restore drill cannot verify it'
        raise NotImplementedError(msg)

    def clear(self, alias: str) -> None:
        """Empty ``alias`` — the drill's throwaway database, once it has been counted."""
        msg = f'{type(self).__name__} cannot empty a database, so the restore drill cannot clean up after itself'
        raise NotImplementedError(msg)

    def connection_settings(self, alias: str) -> Mapping[str, Any]:
        """``HOST``, ``PORT``, ``USER``, ``PASSWORD``, ``NAME``, ``OPTIONS`` of ``alias``."""
        own = self.option('CONNECTIONS')
        if own:
            if alias not in own:
                msg = f"{self.label}['CONNECTIONS'] has no alias {alias!r}"
                raise MisconfiguredError(msg)
            return dict(own[alias])
        if conf.standalone():
            msg = f"{self.label}['CONNECTIONS'] is not set, and there is no Django project to ask for {alias!r}"
            raise MisconfiguredError(msg)
        from django.db import connections  # noqa: PLC0415 - only when a database is touched

        try:
            return connections.databases[alias]
        except KeyError as error:
            msg = f'DATABASES has no alias {alias!r}'
            raise MisconfiguredError(msg) from error

    def same_database(self, one: str, other: str) -> bool:
        """Whether two aliases reach one database: one alias, or the same name on the same host and port."""

        def where(alias: str) -> tuple[str, str, str]:
            found = self.connection_settings(alias)
            host = str(found.get('HOST') or '').strip().lower()
            port = str(found.get('PORT') or '').strip() or self.DEFAULT_PORT
            return 'localhost' if host in _LOOPBACK else host, port, str(found.get('NAME') or '').strip()

        return one == other or where(one) == where(other)
