"""MySQL and MariaDB through ``mysqldump`` / ``mariadb-dump``: one consistent snapshot per database.

``--single-transaction`` reads every InnoDB table as of one moment without locking it (a
MyISAM table is not covered by it: it is read as it is while the dump runs). The connection
comes from ``CONNECTIONS[alias]`` or Django's ``DATABASES[alias]``; the password travels in a 0600 option file passed
as ``--defaults-extra-file`` — never argv, which ``ps`` shows, and not ``MYSQL_PWD``, which
MySQL deprecates. The client needs no Python driver: a restore asks the server through the
same ``mysql`` / ``mariadb`` client whether the target is empty.
"""

import contextlib
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from offsite_backup import findings as checks
from offsite_backup.databases.base import DatabaseSource
from offsite_backup.exceptions import BackupError
from offsite_backup.pipeline import run_into
from offsite_backup.sealing import Sealing

__all__ = ('CLIENTS', 'MySQLSource', 'option_file')

#: ``CLIENT`` -> (the dump program, the client program)
CLIENTS = {'mysql': ('mysqldump', 'mysql'), 'mariadb': ('mariadb-dump', 'mariadb')}
#: the comment both dump programs end a complete dump with (unless told to skip comments)
_COMPLETE = b'-- Dump completed'
_LISTING = (
    'SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = DATABASE() ORDER BY table_name'
)


def _quoted(value: str) -> str:
    """A value for an option file: double-quoted, with backslash and quote escaped."""
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _client_lines(database: Mapping[str, Any]) -> list[str]:
    options = database.get('OPTIONS') or {}
    lines = ['[client]']
    host = str(database.get('HOST') or '')
    socket = str(options.get('unix_socket') or (host if host.startswith('/') else ''))
    if socket:
        lines.append(f'socket={_quoted(socket)}')
    elif host:
        lines.append(f'host={_quoted(host)}')
    if database.get('PORT'):
        lines.append(f'port={int(database["PORT"])}')
    for key, option in (('USER', 'user'), ('PASSWORD', 'password')):
        if database.get(key):
            lines.append(f'{option}={_quoted(str(database[key]))}')
    ssl = options.get('ssl') or {}
    for key, option in (('ca', 'ssl-ca'), ('cert', 'ssl-cert'), ('key', 'ssl-key')):
        if ssl.get(key):
            lines.append(f'{option}={_quoted(str(ssl[key]))}')
    return lines


@contextlib.contextmanager
def option_file(database: Mapping[str, Any]) -> Iterator[tuple[str, str]]:
    """``--defaults-extra-file=<a private file>`` for one connection block, and its database name."""
    with tempfile.TemporaryDirectory(prefix='offsite-mysql-') as directory:
        path = Path(directory) / 'client.cnf'
        path.touch(mode=0o600)
        path.write_text('\n'.join(_client_lines(database)) + '\n')
        yield f'--defaults-extra-file={path}', str(database.get('NAME') or '')


def _identifier(name: str) -> str:
    return '`' + name.replace('`', '``') + '`'


class MySQLSource(DatabaseSource):
    """``ALIASES`` and ``CONNECTIONS`` as for PostgreSQL, ``CLIENT`` mysql|mariadb, ``EXTRA_ARGS`` for the dump."""

    DEFAULT_PORT = '3306'

    @property
    def client(self) -> str:
        """``mysql`` (``mysqldump``, ``mysql``) or ``mariadb`` (``mariadb-dump``, ``mariadb``)."""
        return str(self.option('CLIENT', 'mysql'))

    def binaries(self) -> tuple[str, ...]:
        """The dump program and the client of ``CLIENT``."""
        return CLIENTS.get(self.client, CLIENTS['mysql'])

    def suffix(self) -> str:
        """``sql.zst.age``."""
        return 'sql.zst.age'

    def dump_argv(self, defaults: str, name: str) -> list[str]:
        """The dump command; ``--defaults-extra-file`` must come first."""
        command = [
            self.binaries()[0],
            defaults,
            '--single-transaction',
            '--quick',
            '--hex-blob',
            '--triggers',
            '--no-tablespaces',
            '--default-character-set=utf8mb4',
        ]
        if self.client == 'mysql':
            # a GTID_PURGED line would make the dump refuse to load into any other server
            command.append('--set-gtid-purged=OFF')
        return [*command, *(str(arg) for arg in self.option('EXTRA_ARGS', [])), name]

    def dump(self, alias: str, into: Path, sealing: Sealing) -> Path:
        """``mysqldump | zstd | age`` into ``into``."""
        with option_file(self.connection_settings(alias)) as (defaults, name):
            return run_into([self.dump_argv(defaults, name), *sealing.stages()], into=into)

    def verify(self, plain: Path) -> None:
        """The closing comment is there: a dump that died midway lacks it."""
        with plain.open('rb') as handle:
            handle.seek(max(plain.stat().st_size - 4096, 0))
            if _COMPLETE not in handle.read():
                msg = f'{plain.name} does not end with "{_COMPLETE.decode()}": the dump was cut short'
                raise BackupError(msg)

    def _query(self, defaults: str, name: str, sql: str) -> list[list[str]]:
        """Rows of one statement, through the client (tab-separated, no header)."""
        with tempfile.TemporaryDirectory(prefix='offsite-mysql-') as directory:
            out = Path(directory) / 'rows.tsv'
            command = [self.binaries()[1], defaults, '--batch', '--skip-column-names', f'--execute={sql}', name]
            run_into([command], into=out)
            return [line.split('\t') for line in out.read_text().splitlines() if line]

    def _drop_all(self, defaults: str, name: str, tables: list[list[str]]) -> None:
        drops = [
            f'DROP {"VIEW" if kind == "VIEW" else "TABLE"} IF EXISTS {_identifier(table)}' for table, kind, *_ in tables
        ]
        self._query(defaults, name, '; '.join(['SET FOREIGN_KEY_CHECKS=0', *drops]))

    def tables(self, alias: str) -> dict[str, int]:
        """Every base table of ``alias`` with its exact ``count(*)``, in one statement — what the drill compares."""
        with option_file(self.connection_settings(alias)) as (defaults, name):
            names = [row[0] for row in self._query(defaults, name, _LISTING) if row[1:2] != ['VIEW']]
            if not names:
                return {}
            counts = ' UNION ALL '.join(
                f'SELECT {index}, count(*) FROM {_identifier(table)}'  # noqa: S608 - quoted names from the catalog
                for index, table in enumerate(names)
            )
            found = {int(row[0]): int(row[1]) for row in self._query(defaults, name, counts)}
        return {table: found[index] for index, table in enumerate(names)}

    def clear(self, alias: str) -> None:
        """Drop every table and view of ``alias`` — for the drill's throwaway database only."""
        with option_file(self.connection_settings(alias)) as (defaults, name):
            tables = self._query(defaults, name, _LISTING)
            if tables:
                self._drop_all(defaults, name, tables)

    def restore(self, plain: Path, alias: str, *, clean: bool = False) -> None:
        """Load into ``alias``, which must be empty unless ``clean`` (then every table and view is dropped)."""
        with option_file(self.connection_settings(alias)) as (defaults, name):
            tables = self._query(defaults, name, _LISTING)
            if tables and not clean:
                msg = f'database {alias!r} is not empty ({len(tables)} tables); pass clean=True / --clean to replace it'
                raise BackupError(msg)
            if tables:
                self._drop_all(defaults, name, tables)
            run_into([[self.binaries()[1], defaults, name]], into=Path('/dev/null'), source=plain)

    def check(self) -> list[checks.CheckMessage]:
        """``CLIENT`` is one we know, and its programs are on ``PATH``."""
        found = super().check()
        if self.client not in CLIENTS:
            found.append(
                checks.Error(
                    f"{self.label}['CLIENT'] is {self.client!r}; expected one of {', '.join(CLIENTS)}.",
                    id='offsite_backup.E001',
                )
            )
        return found
