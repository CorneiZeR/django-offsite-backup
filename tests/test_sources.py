import contextlib
import io
import json
import os
import sqlite3
import stat
import sys
import tarfile
from pathlib import Path

import pytest

from offsite_backup.exceptions import BackupError, MisconfiguredError, PipelineError, SourceMissingError
from offsite_backup.media.django_storage import StorageSource
from offsite_backup.media.docker_volume import VolumeSource
from offsite_backup.sealing import Sealing
from offsite_backup.testing import Passthrough, media_tree

SEALING = Sealing(Passthrough({}))


def ids(messages):
    return sorted(message.id for message in messages)


def sealed(source, tmp_path):
    """Every volume a source seals, opened: ``[(names, rows)]``."""
    found = []

    def ship(path, rows):
        with tarfile.open(path) as tar:
            found.append(({member.name: tar.extractfile(member).read() for member in tar}, rows))
        path.unlink()

    source.seal_volumes(lambda number: tmp_path / f'v{number}.tar', SEALING, ship)
    return found


def executable(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# -- Docker volume -------------------------------------------------------------------------


@pytest.fixture
def docker(tmp_path, monkeypatch):
    root = tmp_path / 'docker'
    (root / 'volumes').mkdir(parents=True)
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    script = Path(__file__).with_name('fake_docker.py')
    executable(bin_dir / 'docker', f'#!/bin/sh\nexec {sys.executable} {script} "$@"\n')
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    monkeypatch.setenv('FAKE_DOCKER_ROOT', str(root))

    return Docker(root)


class Docker:
    def __init__(self, root):
        self.root = root

    def __truediv__(self, other):
        return self.root / other

    def calls(self):
        return [json.loads(line) for line in (self.root / 'calls.log').read_text().splitlines()]


def volume(docker, name, files):
    return media_tree(docker / 'volumes' / name, files)


def test_a_docker_volume_is_read_through_a_container_that_never_starts(docker, tmp_path):
    """Created read-only with no network, read with one `docker cp`, removed; never `docker run`/`start`."""
    volume(docker, 'shop_media', {'a.jpg': b'a' * 10, 'sub/b.png': b'b' * 20})
    [(files, rows)] = sealed(VolumeSource({'VOLUME': 'shop_media'}, label='MEDIA'), tmp_path)
    assert files == {'a.jpg': b'a' * 10, 'sub/b.png': b'b' * 20}
    assert [row.path for row in rows] == ['a.jpg', 'sub/b.png']
    verbs = [call[0] if call[0] != 'volume' else 'volume inspect' for call in docker.calls()]
    assert verbs == ['volume inspect', 'create', 'cp', 'rm']
    create = docker.calls()[1]
    assert '--network=none' in create
    assert '--volume=shop_media:/volume:ro' in create
    assert list((docker / 'containers').iterdir()) == []


def test_a_docker_volume_is_cut_into_volumes_while_it_streams(docker, tmp_path):
    """One stream, several volumes, each a whole tar of whole files."""
    volume(docker, 'v', {'a': b'a' * 300, 'b': b'b' * 300, 'c': b'c' * 300})
    source = VolumeSource({'VOLUME': 'v', 'VOLUME_BYTES': 20 * 1024 + 2 * (300 + 2048)}, label='MEDIA')
    assert [sorted(files) for files, _ in sealed(source, tmp_path)] == [['a', 'b'], ['c']]
    assert [call[0] for call in docker.calls()].count('cp') == 1


def test_a_docker_volume_leaves_links_out_and_honours_the_filters(docker, tmp_path):
    """Symbolic links are not followed; INCLUDE/EXCLUDE apply to the names in the volume."""
    root = volume(docker, 'v', {'keep.jpg': b'1', 'cache/x.jpg': b'2', 'skip.txt': b'3'})
    (root / 'link.jpg').symlink_to('/etc/passwd')
    source = VolumeSource({'VOLUME': 'v', 'INCLUDE': ['*.jpg'], 'EXCLUDE': ['cache/*']}, label='MEDIA')
    [(files, _)] = sealed(source, tmp_path)
    assert files == {'keep.jpg': b'1'}


def test_a_missing_docker_volume_is_never_created(docker, tmp_path, caplog):
    """`docker create` would make it, empty; the source asks first and creates nothing."""
    assert sealed(VolumeSource({'VOLUME': 'typo'}, label='MEDIA'), tmp_path) == []
    assert [call[0] for call in docker.calls()] == ['volume']
    assert 'Media volume not found' in caplog.text


def test_a_missing_required_docker_volume_fails_the_pass(docker, tmp_path):
    """REQUIRED turns "nothing there" into a failure naming the source."""
    with pytest.raises(SourceMissingError, match='MEDIA'):
        sealed(VolumeSource({'VOLUME': 'typo', 'REQUIRED': True}, label='MEDIA'), tmp_path)


def test_a_failing_docker_cp_fails_with_its_own_words_and_removes_the_container(docker, tmp_path, monkeypatch):
    """The daemon's refusal is the error, not a tar parse error; the container goes anyway."""
    volume(docker, 'v', {'a': b'1'})
    monkeypatch.setenv('FAKE_DOCKER_CP_FAILS', '1')
    with pytest.raises(PipelineError, match='permission denied'):
        sealed(VolumeSource({'VOLUME': 'v'}, label='MEDIA'), tmp_path)
    assert docker.calls()[-1][0] == 'rm'


def test_docker_volume_deploy_check(docker):
    """W005 for a volume that is not there, E009 when required; nothing for one that is."""
    volume(docker, 'present', {'a': b'1'})
    assert ids(VolumeSource({'VOLUME': 'present'}, label='MEDIA').deploy_check()) == []
    assert ids(VolumeSource({'VOLUME': 'absent'}, label='MEDIA').deploy_check()) == ['offsite_backup.W005']
    required = VolumeSource({'VOLUME': 'absent', 'REQUIRED': True}, label='MEDIA')
    assert ids(required.deploy_check()) == ['offsite_backup.E009']


def test_docker_volume_deploy_check_without_a_daemon_is_e011(tmp_path, monkeypatch):
    """A daemon that cannot be asked is an error of its own, not a missing volume."""
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    executable(bin_dir / 'docker', '#!/bin/sh\necho "Cannot connect to the Docker daemon" >&2\nexit 1\n')
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    assert ids(VolumeSource({'VOLUME': 'v'}, label='MEDIA').deploy_check()) == ['offsite_backup.E011']


def test_docker_volume_needs_the_docker_cli(monkeypatch):
    """E003 names the binary."""
    monkeypatch.setenv('PATH', '/nonexistent')
    assert ids(VolumeSource({'VOLUME': 'v'}, label='MEDIA').check()) == ['offsite_backup.E003']


def test_docker_volume_is_a_whole_pass_source(configured, fake_s3, settings, docker, now):
    """In a pass: volumes and manifests under media/, like any other source."""
    from offsite_backup.pass_ import run

    volume(docker, 'v', {'a.jpg': b'a'})
    settings.OFFSITE_BACKUP['MEDIA'] = [{'BACKEND': 'offsite_backup.media.docker_volume.VolumeSource', 'VOLUME': 'v'}]
    summary = run(now=now)
    assert sum('/media/' in key for key in summary.keys) == 2


def test_docker_volume_requires_its_volume(configured, settings):
    """E001 names VOLUME."""
    from offsite_backup.checks import check_settings

    settings.OFFSITE_BACKUP['MEDIA'] = [{'BACKEND': 'offsite_backup.media.docker_volume.VolumeSource'}]
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E001']
    assert "OFFSITE_BACKUP['MEDIA'][0]['VOLUME']" in error.msg


# -- Django storage ------------------------------------------------------------------------


@pytest.fixture
def storage(settings, tmp_path):
    root = media_tree(tmp_path / 'stored', {'a.jpg': b'a' * 10, 'docs/b.pdf': b'b' * 20, 'docs/deep/c.txt': b'c'})
    settings.STORAGES = {
        **settings.STORAGES,
        'media': {'BACKEND': 'django.core.files.storage.FileSystemStorage', 'OPTIONS': {'location': str(root)}},
    }
    return root


def test_a_django_storage_is_walked_and_streamed(storage, tmp_path):
    """Every file under PREFIX, by its storage name."""
    source = StorageSource({'STORAGE': 'media'}, label='MEDIA')
    [(files, rows)] = sealed(source, tmp_path)
    assert files == {'a.jpg': b'a' * 10, 'docs/b.pdf': b'b' * 20, 'docs/deep/c.txt': b'c'}
    assert [row.path for row in rows] == ['a.jpg', 'docs/b.pdf', 'docs/deep/c.txt']


def test_a_django_storage_prefix_narrows_it(storage, tmp_path):
    """PREFIX is a folder of the storage; names keep it."""
    [(files, _)] = sealed(StorageSource({'STORAGE': 'media', 'PREFIX': '/docs/'}, label='MEDIA'), tmp_path)
    assert sorted(files) == ['docs/b.pdf', 'docs/deep/c.txt']


class Chunked(io.BytesIO):
    """A file that refuses to be read whole."""

    def read(self, size=-1):
        if size is None or size < 0 or size > 1 << 20:
            raise AssertionError('read whole')
        return super().read(size)


class ChunkedStorage:
    def __init__(self, files):
        self.files = files

    def listdir(self, path):
        return [], sorted(self.files)

    def size(self, name):
        return len(self.files[name])

    def get_modified_time(self, name):
        raise NotImplementedError

    def open(self, name, mode='rb'):
        return Chunked(self.files[name])


class FakeObject:
    def __init__(self, data):
        self.data = data

    def get(self):
        return {'Body': Chunked(self.data), 'ContentLength': len(self.data)}


class S3LikeFile(io.BytesIO):
    """django-storages' S3File in shape: ``obj`` is the boto3 object; its own read would spool it whole."""

    def __init__(self, data):
        super().__init__()
        self.obj = FakeObject(data)
        self._file = None

    def read(self, size=-1):
        raise AssertionError('S3File.read downloads the whole object first')


class S3LikeStorage(ChunkedStorage):
    def open(self, name, mode='rb'):
        return S3LikeFile(self.files[name])


@pytest.mark.parametrize('kind', [ChunkedStorage, S3LikeStorage])
def test_a_django_storage_file_is_never_read_whole(kind, tmp_path, monkeypatch):
    """Blocks only; an S3 file is read from its object's body, not through its spooling read()."""
    data = os.urandom(3 << 20)
    source = StorageSource({}, label='MEDIA')
    monkeypatch.setattr(StorageSource, 'storage', kind({'big.bin': data}))
    [(files, rows)] = sealed(source, tmp_path)
    assert files == {'big.bin': data}
    assert rows[0].size == len(data)


def test_a_django_storage_alias_must_exist(settings):
    """E001 for a STORAGE that STORAGES does not define."""
    assert ids(StorageSource({'STORAGE': 'nope'}, label='MEDIA').check()) == ['offsite_backup.E001']
    assert ids(StorageSource({}, label='MEDIA').check()) == []


def test_a_django_storage_that_cannot_be_listed_is_e011(storage, settings):
    """The deploy check lists PREFIX once."""
    assert ids(StorageSource({'STORAGE': 'media'}, label='MEDIA').deploy_check()) == []
    missing = StorageSource({'STORAGE': 'media', 'PREFIX': 'not-there'}, label='MEDIA')
    assert ids(missing.deploy_check()) == ['offsite_backup.E011']


# -- SQLite --------------------------------------------------------------------------------


@pytest.fixture
def lite(tmp_path, monkeypatch):
    from django.db import connections

    path = tmp_path / 'app.sqlite3'
    target = tmp_path / 'restored.sqlite3'
    for alias, name in (('lite', path), ('lite_target', target)):
        monkeypatch.setitem(connections.databases, alias, {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(name)})
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE item (id INTEGER PRIMARY KEY, name TEXT)')
        db.executemany('INSERT INTO item (name) VALUES (?)', [(f'item {n}',) for n in range(500)])
        db.commit()
    return path, target


def sqlite_source(**options):
    from offsite_backup.databases.sqlite import SQLiteSource

    return SQLiteSource({'ALIASES': ['lite'], **options}, label='DATABASES')


def count(path):
    with contextlib.closing(sqlite3.connect(path)) as db:
        return db.execute('SELECT count(*) FROM item').fetchone()[0]


def test_sqlite_round_trip_includes_what_only_the_wal_holds(lite, tmp_path):
    """The online backup carries committed rows not yet checkpointed into the file — a file copy would not."""
    path, target = lite
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA wal_autocheckpoint=0')
    writer.execute("INSERT INTO item (name) VALUES ('only in the wal')")
    writer.commit()
    plain_copy = tmp_path / 'copied.sqlite3'
    plain_copy.write_bytes(path.read_bytes())
    assert count(plain_copy) == 500
    source = sqlite_source()
    dumped = source.dump('lite', tmp_path / 'x.sqlite3.zst.age', SEALING)
    writer.close()
    source.verify(dumped)
    source.restore(dumped, 'lite_target')
    assert count(target) == 501
    assert not list(tmp_path.glob('*.copy'))


def test_sqlite_restore_refuses_a_database_with_tables(lite, tmp_path):
    """--apply without --clean never writes over a database that has tables; with it, it does."""
    path, _ = lite
    source = sqlite_source()
    dumped = source.dump('lite', tmp_path / 'x', SEALING)
    with pytest.raises(BackupError, match='not empty'):
        source.restore(dumped, 'lite')
    source.restore(dumped, 'lite', clean=True)
    assert count(path) == 500


def test_sqlite_verify_refuses_a_cut_short_copy(lite, tmp_path):
    """A truncated file fails its integrity check, and one that is not SQLite says so."""
    source = sqlite_source()
    dumped = source.dump('lite', tmp_path / 'x', SEALING)
    data = dumped.read_bytes()
    dumped.write_bytes(data[: len(data) // 2])
    with pytest.raises(BackupError, match='integrity check'):
        source.verify(dumped)
    dumped.write_bytes(b'-- not sqlite')
    with pytest.raises(BackupError, match='not an SQLite database'):
        source.verify(dumped)


def test_sqlite_missing_database_fails_instead_of_creating_one(lite, tmp_path):
    """A wrong NAME is a failed database, not a new empty one backed up nightly."""
    path, _ = lite
    path.unlink()
    with pytest.raises(SourceMissingError):
        sqlite_source().dump('lite', tmp_path / 'x', SEALING)
    assert not path.exists()


def test_sqlite_checks(lite, monkeypatch):
    """E001 for an alias that is not SQLite or is in memory; W005/E009 for a file not here."""
    from django.db import connections

    monkeypatch.setitem(connections.databases, 'pg', {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'x'})
    monkeypatch.setitem(connections.databases, 'mem', {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'})
    assert ids(sqlite_source().check()) == []
    assert ids(sqlite_source(ALIASES=['pg', 'mem']).check()) == ['offsite_backup.E001', 'offsite_backup.E001']
    lite[0].unlink()
    assert ids(sqlite_source().deploy_check()) == ['offsite_backup.W005']
    assert ids(sqlite_source(REQUIRED=True).deploy_check()) == ['offsite_backup.E009']


def test_sqlite_in_a_pass_and_back(configured, fake_s3, settings, lite, now):
    """The whole cycle through the store: dump, restore into another file, rows equal."""
    from offsite_backup.pass_ import run
    from offsite_backup.restore import restore_database

    settings.OFFSITE_BACKUP['DATABASES'] = {
        'BACKEND': 'offsite_backup.databases.sqlite.SQLiteSource',
        'ALIASES': ['lite'],
    }
    summary = run(now=now)
    assert any(key.endswith('.sqlite3.zst.age') and '/db-lite/' in key for key in summary.keys)
    restore_database('latest', 'lite_target', source_alias='lite', identity=__file__)
    assert count(lite[1]) == 500


# -- MySQL / MariaDB -----------------------------------------------------------------------


@pytest.fixture
def maria(tmp_path, monkeypatch):
    """Fake ``mysqldump``/``mysql`` that record argv and the option file they were handed."""
    from django.db import connections

    monkeypatch.setitem(
        connections.databases,
        'shop',
        {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': 'shop',
            'HOST': 'db.internal',
            'PORT': '3306',
            'USER': 'backup',
            'PASSWORD': 'pa"ss\\word',
        },
    )
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    log = tmp_path / 'calls'
    recorder = (
        f'#!/bin/sh\nfile=$(echo "$1" | sed s/--defaults-extra-file=//)\n'
        f'{{ echo "$(basename $0) $*"; cat "$file"; ls -l "$file"; }} >> {log}\n'
    )
    for client in ('mysqldump', 'mariadb-dump'):
        executable(bin_dir / client, recorder + 'echo "-- MySQL dump"; echo "-- Dump completed on 2026-03-10"\n')
    for client in ('mysql', 'mariadb'):
        executable(bin_dir / client, recorder + 'cat > /dev/null\n')
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    return log


def mysql_source(**options):
    from offsite_backup.databases.mysql import MySQLSource

    return MySQLSource({'ALIASES': ['shop'], **options}, label='DATABASES')


def test_mysql_dump_is_one_transaction_with_the_password_in_a_private_file(maria, tmp_path):
    """--single-transaction; the password is in a 0600 option file, never in argv."""
    dumped = mysql_source().dump('shop', tmp_path / 'x.sql.zst.age', SEALING)
    mysql_source().verify(dumped)
    text = maria.read_text()
    argv = text.splitlines()[0]
    assert argv.startswith('mysqldump --defaults-extra-file=')
    assert '--single-transaction' in argv
    assert '--set-gtid-purged=OFF' in argv
    assert argv.endswith(' shop')
    assert 'pa"ss' not in argv
    assert 'password="pa\\"ss\\\\word"' in text
    assert 'host="db.internal"' in text
    assert '-rw-------' in text
    assert not Path(argv.split()[1].removeprefix('--defaults-extra-file=')).exists()


def test_mariadb_uses_its_own_programs(maria, tmp_path):
    """CLIENT 'mariadb' runs mariadb-dump, without the MySQL-only GTID flag."""
    mysql_source(CLIENT='mariadb').dump('shop', tmp_path / 'x', SEALING)
    argv = maria.read_text().splitlines()[0]
    assert argv.startswith('mariadb-dump ')
    assert '--set-gtid-purged' not in argv


def test_mysql_verify_refuses_a_dump_without_its_closing_line(tmp_path):
    """A dump that died midway lacks "-- Dump completed"."""
    plain = tmp_path / 'x.sql'
    plain.write_text('-- MySQL dump\nINSERT INTO t VALUES (1);\n')
    with pytest.raises(BackupError, match='cut short'):
        mysql_source().verify(plain)


def test_mysql_restore_loads_an_empty_database(maria, tmp_path):
    """The client is asked for tables first; empty, the dump is fed to it."""
    plain = tmp_path / 'x.sql'
    plain.write_text('-- Dump completed\n')
    mysql_source().restore(plain, 'shop')
    calls = [line for line in maria.read_text().splitlines() if line.startswith(('mysql ', 'mariadb '))]
    assert len(calls) == 2
    assert 'information_schema.tables' in calls[0]
    assert calls[1].endswith(' shop')


def test_mysql_restore_refuses_a_database_with_tables_and_clean_drops_them(maria, tmp_path):
    """Not empty: refused; with clean, every table and view is dropped first."""
    client = Path(os.environ['PATH'].split(os.pathsep)[0]) / 'mysql'
    executable(client, client.read_text().replace('cat > /dev/null', "printf 'orders\\tBASE TABLE\\nv`x\\tVIEW\\n'"))
    plain = tmp_path / 'x.sql'
    plain.write_text('-- Dump completed\n')
    with pytest.raises(BackupError, match='not empty'):
        mysql_source().restore(plain, 'shop')
    mysql_source().restore(plain, 'shop', clean=True)
    drops = [line for line in maria.read_text().splitlines() if 'DROP' in line]
    assert len(drops) == 1
    assert 'SET FOREIGN_KEY_CHECKS=0; DROP TABLE IF EXISTS `orders`; DROP VIEW IF EXISTS `v``x`' in drops[0]


def test_mysql_checks(monkeypatch):
    """E001 for an unknown CLIENT; E003 names the programs of the chosen one."""
    assert 'offsite_backup.E001' in ids(mysql_source(CLIENT='oracle').check())
    monkeypatch.setenv('PATH', '/nonexistent')
    found = mysql_source(CLIENT='mariadb').check()
    assert sorted(message.msg.split('`')[1] for message in found) == ['mariadb', 'mariadb-dump']


def test_mysql_socket_host_goes_in_as_a_socket(maria, tmp_path, monkeypatch):
    """A HOST starting with / is a Unix socket, as Django reads it."""
    from django.db import connections

    monkeypatch.setitem(connections.databases['shop'], 'HOST', '/run/mysqld/mysqld.sock')
    mysql_source().dump('shop', tmp_path / 'x', SEALING)
    text = maria.read_text()
    assert 'socket="/run/mysqld/mysqld.sock"' in text
    assert 'host=' not in text


def test_an_unknown_alias_is_named(tmp_path):
    """DATABASES without the alias."""
    with pytest.raises(MisconfiguredError, match='no alias'):
        mysql_source().dump('nope', tmp_path / 'x', SEALING)
