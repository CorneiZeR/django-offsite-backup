"""PostgreSQL through ``pg_dump``: plain SQL by default, or the custom format.

The password reaches ``pg_dump`` in ``PGPASSWORD``, never argv: the command line is
visible in ``ps`` and an error text is posted to a chat. Everything that asks the server a
question — is the target empty, how many rows, which version — goes through ``psql`` with
the same connection, so a server with no Django project is asked the same way.
"""

import os
import re
import subprocess
from pathlib import Path

from offsite_backup import findings as checks
from offsite_backup.databases.base import DatabaseSource
from offsite_backup.exceptions import BackupError, PipelineError
from offsite_backup.pipeline import run_into
from offsite_backup.registry import import_string
from offsite_backup.sealing import Sealing

__all__ = ('PostgresSource',)

FORMATS = ('plain', 'custom')
#: the last line ``pg_dump`` writes to a plain dump; a dump without it was cut short
_COMPLETE = b'PostgreSQL database dump complete'
_VERSION = re.compile(r'(\d+)(?:\.\d+)?')
#: how much of psql's stderr an error carries
_STDERR_TAIL = 2000
#: ordinary tables outside the system schemas; a partition is one, its partitioned parent is not
_TABLES = (
    'SELECT n.nspname, c.relname FROM pg_catalog.pg_class c '
    'JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace '
    "WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname NOT LIKE 'pg\\_%' ORDER BY 1, 2"
)
#: every schema but the system ones dropped, ``public`` made anew: what a throwaway database is left as
_CLEAR = (
    'DO $$DECLARE s text; BEGIN '
    'FOR s IN SELECT nspname FROM pg_catalog.pg_namespace '
    "WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'public') AND nspname NOT LIKE 'pg\\_%' "
    "LOOP EXECUTE format('DROP SCHEMA %I CASCADE', s); END LOOP; END$$; "
    'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;'
)
_PSQL = ['psql', '--no-psqlrc', '--no-password', '--quiet', '--set=ON_ERROR_STOP=1']


def _is_custom(path: Path) -> bool:
    """Whether a decrypted dump is in the custom format, read from its magic bytes."""
    with path.open('rb') as handle:
        return handle.read(5) == b'PGDMP'


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class PostgresSource(DatabaseSource):
    """``ALIASES`` (a list, or a dotted path to a callable returning one), ``FORMAT`` plain|custom, ``CONNECTIONS``."""

    BINARIES = ('pg_dump', 'pg_restore', 'psql')
    DEFAULT_PORT = '5432'

    @property
    def format(self) -> str:
        """``plain`` (default) or ``custom``."""
        return str(self.option('FORMAT', 'plain'))

    def targets(self) -> list[str]:
        """The aliases this run dumps, computing them when ``ALIASES`` is a callable's path."""
        aliases = self.option('ALIASES', ['default'])
        if isinstance(aliases, str):
            aliases = import_string(aliases)()
        return [str(alias).strip() for alias in aliases if str(alias).strip()]

    def suffix(self) -> str:
        """``sql.zst.age`` for plain, ``dump.zst.age`` for custom (``-Z0``, then zstd)."""
        return 'sql.zst.age' if self.format == 'plain' else 'dump.zst.age'

    def connection(self, alias: str) -> tuple[list[str], dict[str, str]]:
        """``--host/--port/--username/--dbname`` and the environment carrying the secrets."""
        database = self.connection_settings(alias)
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

    def psql(self, alias: str, sql: str) -> list[list[str]]:
        """Run ``sql`` against ``alias``; the rows it printed, each split on tabs."""
        argv, env = self.connection(alias)
        command = [*_PSQL, '--tuples-only', '--no-align', '--field-separator=\t', *argv, f'--command={sql}']
        try:
            done = subprocess.run(command, capture_output=True, env={**os.environ, **env}, check=False)
        except OSError as error:
            msg = f'`psql` could not be started: {error.strerror or error}'
            raise PipelineError(msg) from error
        if done.returncode != 0:
            tail = done.stderr.decode(errors='replace').strip()[-_STDERR_TAIL:]
            msg = f'`psql` exited with status {done.returncode} on {alias!r}' + (f': {tail}' if tail else '')
            raise PipelineError(msg)
        return [line.split('\t') for line in done.stdout.decode().splitlines() if line]

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

    def table_names(self, alias: str) -> list[str]:
        """``schema.table`` for every ordinary table of ``alias``."""
        return [f'{row[0]}.{row[1]}' for row in self.psql(alias, _TABLES) if len(row) == 2]  # noqa: PLR2004 - two columns

    def tables(self, alias: str) -> dict[str, int]:
        """Every ordinary table of ``alias`` with ``count(*)`` of it, in one query."""
        names = [row for row in self.psql(alias, _TABLES) if len(row) == 2]  # noqa: PLR2004 - two columns
        if not names:
            return {}
        counts = ' UNION ALL '.join(
            f"SELECT '{index}', count(*) FROM {_quoted(schema)}.{_quoted(table)}"  # noqa: S608 - quoted names from the catalog
            for index, (schema, table) in enumerate(names)
        )
        found = {int(row[0]): int(row[1]) for row in self.psql(alias, counts)}
        return {f'{schema}.{table}': found[index] for index, (schema, table) in enumerate(names)}

    def clear(self, alias: str) -> None:
        """Drop every schema but the system ones and make ``public`` anew — for a throwaway database only."""
        self.psql(alias, _CLEAR)

    def restore(self, plain: Path, alias: str, *, clean: bool = False) -> None:
        """Load into ``alias``, which must be empty unless ``clean`` (then ``public`` is dropped first)."""
        tables = self.table_names(alias)
        if tables and not clean:
            msg = f'database {alias!r} is not empty ({len(tables)} tables); pass clean=True / --clean to replace it'
            raise BackupError(msg)
        argv, env = self.connection(alias)
        if clean:
            self.psql(alias, 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
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
            command = [*_PSQL, '--single-transaction', *argv, f'--file={plain}']
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

    def server_major(self, alias: str) -> int:
        """The major version of the server ``alias`` reaches (``SHOW server_version_num`` // 10000)."""
        rows = self.psql(alias, 'SHOW server_version_num')
        return int(rows[0][0]) // 10000

    def check(self) -> list[checks.CheckMessage]:
        """The format is one we know; ``CONNECTIONS`` maps aliases to blocks."""
        found = super().check()
        if self.format not in FORMATS:
            found.append(
                checks.Error(
                    f"{self.label}['FORMAT'] is {self.format!r}; expected one of {', '.join(FORMATS)}.",
                    id='offsite_backup.E001',
                )
            )
        own = self.option('CONNECTIONS')
        if own is not None and not (isinstance(own, dict) and all(isinstance(block, dict) for block in own.values())):
            found.append(
                checks.Error(
                    f"{self.label}['CONNECTIONS'] is not a mapping of aliases to connection blocks.",
                    hint="{'default': {'HOST': ..., 'PORT': ..., 'USER': ..., 'PASSWORD': ..., 'NAME': ...}}",
                    id='offsite_backup.E001',
                )
            )
        return found

    def deploy_check(self) -> list[checks.CheckMessage]:
        """E004: ``pg_dump`` older than the server refuses to dump it."""
        client = self.client_major()
        found: list[checks.CheckMessage] = []
        for alias in self.targets():
            try:
                server = self.server_major(alias)
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
