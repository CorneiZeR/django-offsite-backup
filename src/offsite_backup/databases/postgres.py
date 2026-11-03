"""PostgreSQL through ``pg_dump``: plain SQL by default, or the custom format.

The password reaches ``pg_dump`` in ``PGPASSWORD``, never argv: the command line is
visible in ``ps`` and an error text is posted to a chat.
"""

import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from django.core import checks
from django.utils.module_loading import import_string

from offsite_backup.databases.base import DatabaseSource
from offsite_backup.exceptions import BackupError, MisconfiguredError
from offsite_backup.pipeline import run_into
from offsite_backup.sealing import Sealing

__all__ = ('PostgresSource',)

FORMATS = ('plain', 'custom')
#: the last line ``pg_dump`` writes to a plain dump; a dump without it was cut short
_COMPLETE = b'PostgreSQL database dump complete'
_VERSION = re.compile(r'(\d+)(?:\.\d+)?')


def _is_custom(path: Path) -> bool:
    """Whether a decrypted dump is in the custom format, read from its magic bytes."""
    with path.open('rb') as handle:
        return handle.read(5) == b'PGDMP'


def _database(alias: str) -> Mapping[str, Any]:
    """The connection settings of ``alias`` — from ``connections``, so aliases added at run time count."""
    from django.db import connections  # noqa: PLC0415 - only when a database is touched

    try:
        return connections.databases[alias]
    except KeyError as error:
        msg = f'DATABASES has no alias {alias!r}'
        raise MisconfiguredError(msg) from error


class PostgresSource(DatabaseSource):
    """``ALIASES`` (a list, or a dotted path to a callable returning one), ``FORMAT`` plain|custom."""

    BINARIES = ('pg_dump', 'pg_restore', 'psql')

    @property
    def format(self) -> str:
        """``plain`` (default) or ``custom``."""
        return str(self.option('FORMAT', 'plain'))

    def targets(self) -> list[str]:
        """The aliases this run dumps, computing them when ``ALIASES`` is a callable's path."""
        aliases = self.option('ALIASES', ['default'])
        if isinstance(aliases, str):
            found: Callable[[], Any] = import_string(aliases)
            aliases = found()
        return [str(alias).strip() for alias in aliases if str(alias).strip()]

    def suffix(self) -> str:
        """``sql.zst.age`` for plain, ``dump.zst.age`` for custom (``-Z0``, then zstd)."""
        return 'sql.zst.age' if self.format == 'plain' else 'dump.zst.age'

    @staticmethod
    def connection(alias: str) -> tuple[list[str], dict[str, str]]:
        """``--host/--port/--username/--dbname`` and the environment carrying the secrets."""
        database = _database(alias)
        argv = []
        for flag, key in (('--host', 'HOST'), ('--port', 'PORT'), ('--username', 'USER')):
            if database.get(key):
                argv.append(f'{flag}={database[key]}')
        argv.append(f'--dbname={database.get("NAME") or ""}')
        env = {'PGPASSWORD': str(database.get('PASSWORD') or ''), 'PGAPPNAME': 'offsite_backup'}
        options = database.get('OPTIONS') or {}
        for option, variable in (('sslmode', 'PGSSLMODE'), ('sslrootcert', 'PGSSLROOTCERT')):
            if options.get(option):
                env[variable] = str(options[option])
        return argv, env

    def dump_argv(self, alias: str) -> tuple[list[str], dict[str, str]]:
        """The ``pg_dump`` command for ``alias`` and its environment."""
        argv, env = self.connection(alias)
        command = ['pg_dump', '--no-password', *argv]
        if self.option('NO_OWNER', default=True):
            command += ['--no-owner', '--no-privileges']
        if self.format == 'custom':
            # zlib off: zstd compresses better, and twice-compressed data does not shrink
            command += ['--format=custom', '--compress=0']
        command += [str(arg) for arg in self.option('EXTRA_ARGS', [])]
        return command, env

    def dump(self, alias: str, into: Path, sealing: Sealing) -> Path:
        """``pg_dump | zstd | age`` into ``into``."""
        command, env = self.dump_argv(alias)
        return run_into([command, *sealing.stages()], into=into, env=env)

    def verify(self, plain: Path) -> None:
        """Plain: the closing line is there. Custom: ``pg_restore --list`` reads the whole table of contents."""
        if _is_custom(plain):
            run_into([['pg_restore', '--list', str(plain)]], into=Path('/dev/null'))
            return
        with plain.open('rb') as handle:
            handle.seek(max(plain.stat().st_size - 4096, 0))
            if _COMPLETE not in handle.read():
                msg = f'{plain.name} does not end with "{_COMPLETE.decode()}": the dump was cut short'
                raise BackupError(msg)

    def restore(self, plain: Path, alias: str, *, clean: bool = False) -> None:
        """Load into ``alias``, which must be empty unless ``clean`` (then ``public`` is dropped first)."""
        from django.db import connections  # noqa: PLC0415 - only when restoring

        tables = connections[alias].introspection.table_names()
        if tables and not clean:
            msg = f'database {alias!r} is not empty ({len(tables)} tables); pass clean=True / --clean to replace it'
            raise BackupError(msg)
        argv, env = self.connection(alias)
        if clean:
            run_into(
                [
                    [
                        'psql',
                        '--no-psqlrc',
                        '--no-password',
                        '--quiet',
                        '--set=ON_ERROR_STOP=1',
                        *argv,
                        '--command=DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;',
                    ]
                ],
                into=Path('/dev/null'),
                env=env,
            )
        if _is_custom(plain):
            command = [
                'pg_restore',
                '--no-password',
                '--no-owner',
                '--no-privileges',
                '--exit-on-error',
                '--single-transaction',
                *argv,
                str(plain),
            ]
        else:
            command = [
                'psql',
                '--no-psqlrc',
                '--no-password',
                '--quiet',
                '--set=ON_ERROR_STOP=1',
                '--single-transaction',
                *argv,
                f'--file={plain}',
            ]
        run_into([command], into=Path('/dev/null'), env=env)

    @staticmethod
    def client_major() -> int | None:
        """The major version of the ``pg_dump`` on PATH."""
        try:
            out = subprocess.run(['pg_dump', '--version'], capture_output=True, text=True, check=True).stdout
        except (OSError, subprocess.CalledProcessError):
            return None
        found = _VERSION.search(out.rsplit(' ', 1)[-1]) or _VERSION.search(out)
        return int(found.group(1)) if found else None

    def check(self) -> list[checks.CheckMessage]:
        """The format is one we know; ``ALIASES`` is a list or a path."""
        found = super().check()
        if self.format not in FORMATS:
            found.append(
                checks.Error(
                    f"{self.label}['FORMAT'] is {self.format!r}; expected one of {', '.join(FORMATS)}.",
                    id='offsite_backup.E001',
                )
            )
        return found

    def deploy_check(self) -> list[checks.CheckMessage]:
        """E004: ``pg_dump`` older than the server refuses to dump it."""
        from django.db import connections  # noqa: PLC0415 - a deploy check may connect

        client = self.client_major()
        found: list[checks.CheckMessage] = []
        for alias in self.targets():
            try:
                connection = connections[alias]
                connection.ensure_connection()
                server = int(getattr(connection, 'pg_version', 0)) // 10000
            except Exception:  # noqa: BLE001, S112 - an unreachable database is not this check's finding
                continue
            if client is not None and server and client < server:
                found.append(
                    checks.Error(
                        f'pg_dump is {client}, older than the server of {alias!r} ({server}).',
                        hint=f'Install the PostgreSQL {server} client (e.g. postgresql-client-{server}) in the image.',
                        id='offsite_backup.E004',
                    )
                )
        return found
