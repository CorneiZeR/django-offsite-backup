"""The new sources and stores under the restore drill and the command line."""

import contextlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from offsite_backup import conf
from offsite_backup.cli import main
from offsite_backup.exceptions import MisconfiguredError
from offsite_backup.media.django_storage import StorageSource
from tests import backends
from tests.test_without_django import without_django

ROWS = 300


def make_sqlite(path, rows=ROWS):
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT)')
        db.execute('CREATE VIEW first AS SELECT * FROM item WHERE id < 10')
        db.executemany('INSERT INTO item (name) VALUES (?)', [(f'item {n}',) for n in range(rows)])
        db.commit()
    return path


def objects(path):
    with contextlib.closing(sqlite3.connect(path)) as db:
        return sorted(row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))


def ids(messages):
    return sorted(message.id for message in messages)


def executable(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# -- the drill -----------------------------------------------------------------------------


@pytest.fixture
def lite_drill(configured, settings, tmp_path, monkeypatch, now):
    from offsite_backup.pass_ import run

    live = make_sqlite(tmp_path / 'app.sqlite3')
    settings.OFFSITE_BACKUP['DATABASES'] = {
        'BACKEND': 'offsite_backup.databases.sqlite.SQLiteSource',
        'ALIASES': ['default'],
        'CONNECTIONS': {'default': {'NAME': str(live)}, 'drill': {'NAME': str(tmp_path / 'drill.sqlite3')}},
    }
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': 'drill'}
    monkeypatch.setenv('AGE_IDENTITY', 'AGE-SECRET-KEY-TEST')
    run(now=now)
    return tmp_path


def test_the_drill_restores_an_sqlite_database_counts_it_and_empties_it(lite_drill, now):
    """SQLite answers the drill's questions: every table counted there and live, the target emptied after."""
    from offsite_backup.drill import run_drill

    report = run_drill(now=now)
    [found] = report.databases
    assert (found.tables, found.rows, found.live_rows) == (1, ROWS, ROWS)
    assert objects(lite_drill / 'drill.sqlite3') == []
    assert objects(lite_drill / 'app.sqlite3') == ['first', 'item']


def test_the_drill_refuses_the_live_sqlite_file_under_another_name(lite_drill, settings, now):
    """One file is one database, whatever path names it."""
    from offsite_backup.drill import run_drill

    other = lite_drill / 'sub' / '..' / 'app.sqlite3'
    (lite_drill / 'sub').mkdir()
    settings.OFFSITE_BACKUP['DATABASES']['CONNECTIONS']['drill'] = {'NAME': str(other)}
    with pytest.raises(MisconfiguredError, match='the live database'):
        run_drill(now=now)
    assert objects(lite_drill / 'app.sqlite3') == ['first', 'item']


@pytest.fixture
def mysql_client(tmp_path, monkeypatch):
    """A fake ``mysql`` answering the listing and the counts, logging every statement."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'statements'
    script = f"""#!/bin/sh
for arg in "$@"; do case "$arg" in --execute=*) sql="${{arg#--execute=}}";; esac; done
echo "$sql" >> {log}
case "$sql" in
  *"UNION ALL"*|"SELECT 0, count"*) printf '0\\t3\\n1\\t5\\n';;
  *information_schema*) printf 'orders\\tBASE TABLE\\nrecent\\tVIEW\\nusers\\tBASE TABLE\\n';;
esac
"""
    executable(bin_dir / 'mysql', script)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    return log


def mysql_source():
    from offsite_backup.databases.mysql import MySQLSource

    return MySQLSource(
        {'CONNECTIONS': {'shop': {'HOST': 'db', 'NAME': 'shop', 'USER': 'u', 'PASSWORD': 'p'}}}, label='DATABASES'
    )


def test_mysql_counts_every_base_table_for_the_drill(mysql_client):
    """Views are not tables to count; the counts come back by name."""
    assert mysql_source().tables('shop') == {'orders': 3, 'users': 5}
    assert '`recent`' not in mysql_client.read_text().splitlines()[-1]


def test_mysql_clears_every_table_and_view_for_the_drill(mysql_client):
    """The throwaway database is left with nothing in it."""
    mysql_source().clear('shop')
    assert mysql_client.read_text().splitlines()[-1] == (
        'SET FOREIGN_KEY_CHECKS=0; DROP TABLE IF EXISTS `orders`; DROP VIEW IF EXISTS `recent`; '
        'DROP TABLE IF EXISTS `users`'
    )


def test_mysql_default_port_is_one_server():
    """An empty PORT and 3306 reach the same database: the drill refuses that target."""
    from offsite_backup.databases.mysql import MySQLSource

    source = MySQLSource(
        {
            'CONNECTIONS': {
                'live': {'HOST': '127.0.0.1', 'NAME': 'shop'},
                'drill': {'HOST': 'localhost', 'PORT': 3306, 'NAME': 'shop'},
            }
        }
    )
    assert source.same_database('live', 'drill')


# -- without a Django project --------------------------------------------------------------


@pytest.fixture
def standalone():
    conf.configure({'PREFIX': 'p'})
    yield
    conf.configure(None)


def test_a_django_storage_without_a_django_project_is_refused_by_name(standalone):
    """E002 at check time, and a named refusal at pass time — never an ImportError from deep inside."""
    source = StorageSource({}, label="OFFSITE_BACKUP['MEDIA'][0]")
    [error] = source.check()
    assert error.id == 'offsite_backup.E002'
    assert 'needs a Django project' in error.msg
    with pytest.raises(MisconfiguredError, match='needs a Django project'):
        list(source.entries())


TOML = """
PREFIX = "acme/registry"
COMPRESS = []

[[STORE]]
NAME = "offsite"
BACKEND = "offsite_backup.stores.filesystem.DirectoryStore"
ROOT = "{root}/offsite"

[[STORE]]
NAME = "local"
BACKEND = "offsite_backup.stores.filesystem.DirectoryStore"
ROOT = "{root}/local"
KEEP_NIGHTS = 2

[ENCRYPTION]
BACKEND = "offsite_backup.testing.Passthrough"
RECIPIENTS = "age1test"

[DATABASES]
BACKEND = "offsite_backup.databases.sqlite.SQLiteSource"
ALIASES = ["app", "reports"]
CONNECTIONS = {{ app = {{ NAME = "{root}/app.sqlite3" }}, reports = {{ NAME = "{root}/reports.sqlite3" }} }}

[[MEDIA]]
BACKEND = "offsite_backup.media.filesystem.DirectorySource"
ROOT = "{root}/media"
"""


@pytest.fixture
def plain_server(tmp_path, monkeypatch):
    make_sqlite(tmp_path / 'app.sqlite3')
    make_sqlite(tmp_path / 'reports.sqlite3', rows=7)
    (tmp_path / 'media' / 'docs').mkdir(parents=True)
    (tmp_path / 'media' / 'docs' / 'a.pdf').write_bytes(b'pdf')
    (tmp_path / 'media' / 'b.jpg').write_bytes(b'jpg')
    setting = tmp_path / 'offsite-backup.toml'
    setting.write_text(TOML.format(root=tmp_path))
    monkeypatch.setenv('AGE_IDENTITY', 'AGE-SECRET-KEY-TEST')
    return setting


def test_the_command_line_writes_to_every_store(plain_server, capsys):
    """run counts each object once and names how many stores took it."""
    assert main(['--config', str(plain_server), 'run']) == 0
    assert 'offsite-backup: 4 objects' in capsys.readouterr().out
    assert sorted(p.name for p in (plain_server.parent / 'local').rglob('*.age')) == sorted(
        p.name for p in (plain_server.parent / 'offsite').rglob('*.age')
    )


def test_the_command_line_lists_the_named_store(plain_server, capsys):
    """list --store, and the JSON says which."""
    main(['--config', str(plain_server), 'run'])
    capsys.readouterr()
    assert main(['--config', str(plain_server), 'list', '--store', 'local', '--json']) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index('{') :])['store'] == 'local'


def test_the_command_line_restores_one_media_file(plain_server, tmp_path, capsys):
    """restore --path, verified against the manifest, from the store named."""
    main(['--config', str(plain_server), 'run'])
    capsys.readouterr()
    into = tmp_path / 'out'
    code = main(
        [
            '--config',
            str(plain_server),
            'restore',
            '--stamp',
            'latest',
            '--path',
            'docs',
            '--store',
            'local',
            '--into',
            str(into),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out.strip().endswith('docs/a.pdf  (verified)')
    assert (into / 'media' / 'docs' / 'a.pdf').read_bytes() == b'pdf'
    assert not (into / 'media' / 'b.jpg').exists()


def test_the_command_line_database_narrows_kind(plain_server, tmp_path, capsys):
    """restore --database: that database alone; outside --kind, refused by name."""
    main(['--config', str(plain_server), 'run'])
    capsys.readouterr()
    assert (
        main(
            [
                '--config',
                str(plain_server),
                'restore',
                '--stamp',
                'latest',
                '--database',
                'reports',
                '--into',
                str(tmp_path / 'o'),
            ]
        )
        == 0
    )
    [line] = capsys.readouterr().out.splitlines()
    assert '/db-reports/' in line
    assert (
        main(
            [
                '--config',
                str(plain_server),
                'restore',
                '--stamp',
                'latest',
                '--kind',
                'media',
                '--database',
                'reports',
                '--into',
                str(tmp_path / 'refused'),
            ]
        )
        == 1
    )
    assert 'leaves out' in capsys.readouterr().err


def test_new_sources_run_from_the_command_line_with_django_blocked(plain_server, tmp_path):
    """SQLite, a Docker volume and pyrage need no Django; a Django storage is refused by its name."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    script = Path(__file__).with_name('fake_docker.py')
    executable(bin_dir / 'docker', f'#!/bin/sh\nexec {sys.executable} {script} "$@"\n')
    docker_root = tmp_path / 'docker'
    (docker_root / 'volumes' / 'shop_media').mkdir(parents=True)
    (docker_root / 'volumes' / 'shop_media' / 'c.png').write_bytes(b'png')
    text = plain_server.read_text().replace(
        '[[MEDIA]]',
        '[[MEDIA]]\nNAME = "volume"\nBACKEND = "offsite_backup.media.docker_volume.VolumeSource"\nVOLUME = "shop_media"\n\n[[MEDIA]]',
    )
    pyrage = pytest.importorskip('pyrage')
    text = text.replace('"offsite_backup.testing.Passthrough"', '"offsite_backup.encryption.pyrage.Pyrage"')
    text = text.replace('"age1test"', f'"{pyrage.x25519.Identity.generate().to_public()}"')
    plain_server.write_text(text)
    storage = tmp_path / 'storage.toml'
    storage.write_text(
        text.replace(
            'BACKEND = "offsite_backup.media.filesystem.DirectorySource"',
            'BACKEND = "offsite_backup.media.django_storage.StorageSource"',
        )
    )
    env = {**os.environ, 'PATH': f'{bin_dir}{os.pathsep}{os.environ["PATH"]}', 'FAKE_DOCKER_ROOT': str(docker_root)}
    code = """
        import os, sys
        os.environ.update({'PATH': sys.argv[3], 'FAKE_DOCKER_ROOT': sys.argv[4]})
        from offsite_backup.cli import main
        codes = [main(['--config', sys.argv[1], 'run']), main(['--config', sys.argv[2], 'checks'])]
        assert 'django' not in sys.modules, 'django was imported'
        print(codes)
        """
    result = without_django(code, str(plain_server), str(storage), env['PATH'], env['FAKE_DOCKER_ROOT'])
    assert result.stdout.strip().splitlines()[-1] == '[0, 1]', result.stderr
    # Django absent altogether: the registry names the extra that brings it
    assert "['BACKEND'] is StorageSource, but its driver is not installed" in result.stderr
    assert 'django-offsite-backup[django]' in result.stderr
    kinds = {p.parent.name for p in (tmp_path / 'offsite').rglob('*.age')}
    assert kinds == {'db-app', 'db-reports', 'media', 'media-volume'}
    assert backends.DUMPED == []
