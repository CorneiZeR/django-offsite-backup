"""PostgresSource without a server: a ``psql`` on PATH that answers from a script and logs what it was given."""

import json
import os
import stat
import sys
import textwrap

import pytest

from offsite_backup import conf
from offsite_backup.databases.postgres import PostgresSource
from offsite_backup.exceptions import BackupError, MisconfiguredError, PipelineError

CONNECTIONS = {'default': {'HOST': 'db', 'PORT': '5433', 'USER': 'svc', 'PASSWORD': 's3cret', 'NAME': 'app'}}


@pytest.fixture
def psql(tmp_path, monkeypatch):
    """``psql`` that prints ``TABLES`` for the catalog query, a count per table, or fails on ``FAIL``."""
    log = tmp_path / 'psql.log'
    script = tmp_path / 'bin' / 'psql'
    script.parent.mkdir()
    script.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json, os, sys
            command = sys.stdin.read()
            with open({str(log)!r}, 'a') as log:
                log.write(json.dumps({{'argv': sys.argv[1:], 'sql': command, 'password': os.environ.get('PGPASSWORD')}}) + '\\n')
            if 'FAIL' in os.environ.get('FAKE_PSQL', ''):
                sys.stderr.write('connection refused')
                sys.exit(2)
            if 'pg_catalog.pg_class' in command:
                for row in os.environ.get('FAKE_TABLES', '').split(','):
                    if row:
                        print(row.replace('.', '\\t', 1))
            elif 'UNION ALL' in command or command.startswith('SELECT'):
                for index, part in enumerate(command.split(' UNION ALL ')):
                    print(f'{{index}}\\t{{(index + 1) * 10}}')
            elif 'server_version_num' in command:
                print('180001')
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv('PATH', f'{script.parent}{os.pathsep}{os.environ["PATH"]}')

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return calls


def source(**options):
    return PostgresSource({'CONNECTIONS': CONNECTIONS, **options}, label="OFFSITE_BACKUP['DATABASES']")


def test_tables_counts_every_table_in_one_query(psql, monkeypatch):
    """The catalog lists the tables; one UNION ALL counts them all."""
    monkeypatch.setenv('FAKE_TABLES', 'public.a,public.b,audit.log')
    assert source().tables('default') == {'public.a': 10, 'public.b': 20, 'audit.log': 30}
    assert len(psql()) == 2


def test_an_empty_database_counts_nothing(psql, monkeypatch):
    """No table, no second query."""
    monkeypatch.setenv('FAKE_TABLES', '')
    assert source().tables('default') == {}
    assert len(psql()) == 1


def test_the_connection_comes_from_connections_and_the_password_from_the_environment(psql, monkeypatch):
    """CONNECTIONS gives host, port, user and name in argv; the password only in PGPASSWORD."""
    monkeypatch.setenv('FAKE_TABLES', '')
    source().tables('default')
    [call] = psql()
    assert {'--host=db', '--port=5433', '--username=svc', '--dbname=app'} <= set(call['argv'])
    assert 's3cret' not in ' '.join(call['argv'])
    assert call['password'] == 's3cret'


def test_a_failing_psql_is_named_with_its_stderr(psql, monkeypatch):
    """The status and the tail of stderr, and the alias."""
    monkeypatch.setenv('FAKE_PSQL', 'FAIL')
    with pytest.raises(PipelineError, match="`psql` exited with status 2 on 'default': connection refused"):
        source().tables('default')


def test_restore_refuses_a_non_empty_target_asked_through_psql(psql, monkeypatch, tmp_path):
    """The emptiness check needs no Django connection."""
    monkeypatch.setenv('FAKE_TABLES', 'public.a')
    plain = tmp_path / 'x.sql'
    plain.write_text('-- PostgreSQL database dump complete\n')
    with pytest.raises(BackupError, match="database 'default' is not empty"):
        source().restore(plain, 'default')


def test_clear_drops_every_schema_and_makes_public_anew(psql):
    """The drill's clean-up is one statement against the target."""
    source().clear('default')
    [call] = psql()
    command = call['sql']
    assert 'DROP SCHEMA %I CASCADE' in command
    assert command.endswith('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')


def test_the_statement_travels_on_stdin_not_in_argv(psql, monkeypatch):
    """A count per table of thousands of tables would not fit in one argument."""
    monkeypatch.setenv('FAKE_TABLES', ','.join(f'tenant_{n}.orders' for n in range(3000)))
    assert len(source().tables('default')) == 3000
    for call in psql():
        assert not any(arg.startswith('--command') for arg in call['argv'])


def test_server_major_is_asked_through_psql(psql):
    """E004's question works off Django too."""
    assert source().server_major('default') == 18


def test_an_alias_connections_does_not_have_is_named():
    """A typo in an alias is refused by name."""
    with pytest.raises(MisconfiguredError, match=r"\['CONNECTIONS'\] has no alias 'other'"):
        source().connection_settings('other')


def test_without_connections_and_without_django_the_alias_cannot_be_resolved():
    """Off Django there is nobody else to ask: CONNECTIONS is named."""
    conf.configure({'PREFIX': 'p'})
    try:
        with pytest.raises(MisconfiguredError, match=r"\['CONNECTIONS'\] is not set"):
            PostgresSource({}, label='D').connection_settings('default')
    finally:
        conf.configure(None)


@pytest.mark.parametrize('bad', [['default'], {'default': 'postgres://x'}])
def test_connections_of_the_wrong_shape_is_e001(bad):
    """CONNECTIONS maps aliases to blocks."""
    found = PostgresSource({'CONNECTIONS': bad}, label='D').check()
    assert any("['CONNECTIONS']" in message.msg and message.id == 'offsite_backup.E001' for message in found)


def test_one_database_on_another_port_is_another_database():
    """Same host and name, a different port: a scratch server beside the live one is allowed."""
    connections = {'live': {'HOST': 'db', 'NAME': 'app'}, 'scratch': {'HOST': 'db', 'PORT': '55432', 'NAME': 'app'}}
    assert not PostgresSource({'CONNECTIONS': connections}).same_database('scratch', 'live')
