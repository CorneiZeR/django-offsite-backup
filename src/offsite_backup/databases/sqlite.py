"""SQLite through Python's online backup API: a consistent copy while the application writes.

``sqlite3.Connection.backup`` copies the database page by page and starts over when a
writer changes a page it has copied, so the copy is one moment of the database — copying
the file itself could catch a transaction halfway, or miss what sits in the WAL. The copy
lands in the pass's private workspace, is compressed and sealed from there, and removed.
No binary is needed: ``sqlite3`` is in the standard library.
"""

import contextlib
import sqlite3
from pathlib import Path

from offsite_backup import conf
from offsite_backup import findings as checks
from offsite_backup.databases.base import DatabaseSource
from offsite_backup.exceptions import BackupError, MisconfiguredError, SourceMissingError
from offsite_backup.pipeline import run_into
from offsite_backup.sealing import Sealing

__all__ = ('SQLiteSource',)

#: the first 16 bytes of every SQLite database file
_HEADER = b'SQLite format 3\x00'


def _tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
    return [str(row[0]) for row in rows]


class SQLiteSource(DatabaseSource):
    """``ALIASES`` of SQLite databases in ``DATABASES`` (or ``CONNECTIONS``); each ``NAME`` is a file."""

    def suffix(self) -> str:
        """``sqlite3.zst.age``: the database file itself, compressed and sealed."""
        return 'sqlite3.zst.age'

    def path(self, alias: str) -> Path:
        """The database file of ``alias``; an in-memory database is refused."""
        database = self.connection_settings(alias)
        # a CONNECTIONS block names no engine; a Django one must be SQLite's
        if database.get('ENGINE') and 'sqlite3' not in str(database['ENGINE']):
            msg = f'DATABASES[{alias!r}] is not an SQLite database'
            raise MisconfiguredError(msg)
        name = str(database.get('NAME') or '')
        if not name or name == ':memory:' or 'mode=memory' in name:
            msg = f'DATABASES[{alias!r}] is an in-memory database: there is no file to back up'
            raise MisconfiguredError(msg)
        return Path(name).expanduser()

    def dump(self, alias: str, into: Path, sealing: Sealing) -> Path:
        """The online backup into the workspace, then ``zstd | age`` into ``into``."""
        source = self.path(alias)
        if not source.is_file():
            msg = f'{self.label}: the SQLite database {str(source)!r} of {alias!r} does not exist'
            raise SourceMissingError(msg)
        copy = into.with_name(into.name + '.copy')
        try:
            # read-only, so a missing file is an error rather than a new, empty database
            with (
                contextlib.closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as live,
                contextlib.closing(sqlite3.connect(copy)) as target,
            ):
                live.backup(target)
            return run_into(sealing.stages(), into=into, source=copy)
        finally:
            copy.unlink(missing_ok=True)

    def verify(self, plain: Path) -> None:
        """An SQLite file whose ``PRAGMA integrity_check`` says ``ok``."""
        with plain.open('rb') as handle:
            if handle.read(len(_HEADER)) != _HEADER:
                msg = f'{plain.name} is not an SQLite database'
                raise BackupError(msg)
        try:
            with contextlib.closing(sqlite3.connect(plain.resolve().as_uri() + '?mode=ro', uri=True)) as copy:
                result = [str(row[0]) for row in copy.execute('PRAGMA integrity_check')]
        except sqlite3.DatabaseError as error:  # a file cut short is "malformed" before it is checked
            result = [str(error)]
        if result != ['ok']:
            msg = f'{plain.name} fails its integrity check: {"; ".join(result[:3])}'
            raise BackupError(msg)

    def restore(self, plain: Path, alias: str, *, clean: bool = False) -> None:
        """Copy the backup over ``alias``'s file, which must hold no table unless ``clean``."""
        target = self.path(alias)
        if target.is_file():
            with contextlib.closing(sqlite3.connect(target)) as existing:
                tables = _tables(existing)
            if tables and not clean:
                msg = f'database {alias!r} is not empty ({len(tables)} tables); pass clean=True / --clean to replace it'
                raise BackupError(msg)
        if not conf.standalone() and not self.option('CONNECTIONS'):
            from django.db import connections  # noqa: PLC0415 - only when Django holds the connection

            connections[alias].close()
        with (
            contextlib.closing(sqlite3.connect(plain.resolve().as_uri() + '?mode=ro', uri=True)) as backup,
            contextlib.closing(sqlite3.connect(target)) as live,
        ):
            backup.backup(live)

    def tables(self, alias: str) -> dict[str, int]:
        """Every table of ``alias`` with its exact ``count(*)`` — what the drill compares."""
        with contextlib.closing(sqlite3.connect(self.path(alias).resolve().as_uri() + '?mode=ro', uri=True)) as db:
            return {
                name: int(db.execute(f'SELECT count(*) FROM "{name.replace(chr(34), chr(34) * 2)}"').fetchone()[0])  # noqa: S608 - names from the catalog
                for name in _tables(db)
            }

    def clear(self, alias: str) -> None:
        """Drop every view, trigger and table of ``alias`` — for the drill's throwaway database only."""
        path = self.path(alias)
        if not path.is_file():
            return
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute('PRAGMA foreign_keys = OFF')
            rows = db.execute(
                "SELECT type, name FROM sqlite_master WHERE type IN ('view', 'trigger', 'table') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 1 ELSE 0 END"
            ).fetchall()
            for kind, name in rows:
                db.execute(f'DROP {kind.upper()} IF EXISTS "{name.replace(chr(34), chr(34) * 2)}"')
            db.commit()

    def same_database(self, one: str, other: str) -> bool:
        """One file, whatever the path that names it."""
        try:
            return one == other or self.path(one).resolve() == self.path(other).resolve()
        except MisconfiguredError:
            return one == other

    def check(self) -> list[checks.CheckMessage]:
        """Each listed alias is an SQLite database with a file (a computed list is checked when it runs)."""
        found = super().check()
        aliases = self.option('ALIASES', ['default'])
        if isinstance(aliases, str):
            return found
        for alias in self.targets():
            try:
                self.path(alias)
            except MisconfiguredError as error:  # noqa: PERF203 - one finding per alias
                found.append(checks.Error(f'{self.label}: {error}.', id='offsite_backup.E001'))
        return found

    def deploy_check(self) -> list[checks.CheckMessage]:
        """W005 (E009 when required): a database file that is not here, where a pass runs."""
        found = super().deploy_check()
        for alias in self.targets():
            try:
                path = self.path(alias)
            except MisconfiguredError:
                continue  # the settings check names it
            if not path.is_file():
                text = f'{self.label}: the SQLite database {str(path)!r} of {alias!r} does not exist here.'
                hint = 'Mount the directory holding the database into the container that runs the backup.'
                if self.required:
                    found.append(checks.Error(text, hint=hint, id='offsite_backup.E009'))
                else:
                    found.append(checks.Warning(text, hint=hint, id='offsite_backup.W005'))
        return found
