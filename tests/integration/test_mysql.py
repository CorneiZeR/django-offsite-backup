"""Against a real MySQL or MariaDB server, with its own client, ``age`` and ``zstd``.

Marked ``mysql`` and deselected from the PostgreSQL legs; run with
``OFFSITE_BACKUP_TEST_MYSQL_PORT`` set (and ``OFFSITE_BACKUP_TEST_MYSQL_CLIENT=mariadb`` for
MariaDB). No skip at module level: a deselected module must not report a skip.
"""

import os
import shutil
import subprocess

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.mysql]

CLIENT = os.environ.get('OFFSITE_BACKUP_TEST_MYSQL_CLIENT', 'mysql')
SERVER = {
    'ENGINE': 'django.db.backends.mysql',
    'HOST': os.environ.get('OFFSITE_BACKUP_TEST_MYSQL_HOST', '127.0.0.1'),
    'PORT': os.environ.get('OFFSITE_BACKUP_TEST_MYSQL_PORT', ''),
    'USER': os.environ.get('OFFSITE_BACKUP_TEST_MYSQL_USER', 'root'),
    'PASSWORD': os.environ.get('OFFSITE_BACKUP_TEST_MYSQL_PASSWORD', 'ob'),
}
ROWS = 3000


@pytest.fixture
def server(monkeypatch):
    from django.db import connections

    from offsite_backup.databases.mysql import CLIENTS

    if not SERVER['PORT']:
        pytest.skip('OFFSITE_BACKUP_TEST_MYSQL_PORT is not set')
    for binary in (*CLIENTS[CLIENT], 'age', 'age-keygen', 'zstd'):
        if shutil.which(binary) is None:
            pytest.skip(f'{binary} is not installed')
    for alias in ('source', 'target'):
        monkeypatch.setitem(connections.databases, f'my_{alias}', {**SERVER, 'NAME': f'ob_{alias}'})
    sql(
        None,
        'DROP DATABASE IF EXISTS ob_source; DROP DATABASE IF EXISTS ob_target; '
        'CREATE DATABASE ob_source CHARACTER SET utf8mb4; CREATE DATABASE ob_target CHARACTER SET utf8mb4',
    )
    sql(
        'ob_source',
        'CREATE TABLE customer (id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(100), note TEXT, photo BLOB) '
        'ENGINE=InnoDB; '
        'CREATE TABLE purchase (id INT AUTO_INCREMENT PRIMARY KEY, customer_id INT, total DECIMAL(12,2), '
        'FOREIGN KEY (customer_id) REFERENCES customer(id)) ENGINE=InnoDB; '
        'CREATE VIEW big_purchase AS SELECT * FROM purchase WHERE total > 100',
    )
    values = ', '.join(f"('customer {n}', 'ünïcødé {n}', UNHEX('00FF{n % 256:02X}'))" for n in range(ROWS))
    sql('ob_source', f'INSERT INTO customer (name, note, photo) VALUES {values}')
    sql('ob_source', 'INSERT INTO purchase (customer_id, total) SELECT id, id * 1.25 FROM customer')


def sql(database, statement):
    from offsite_backup.databases.mysql import CLIENTS

    env = {**os.environ, 'MYSQL_PWD': SERVER['PASSWORD']}
    argv = [CLIENTS[CLIENT][1], f'--host={SERVER["HOST"]}', f'--port={SERVER["PORT"]}', f'--user={SERVER["USER"]}']
    argv += ['--batch', '--skip-column-names', *([database] if database else [])]
    # on stdin: one argument stops at 128 KiB on Linux
    return subprocess.run(argv, input=statement, capture_output=True, text=True, check=True, env=env).stdout


def snapshot(database):
    return {
        table: sql(database, f'SELECT count(*), sum(crc32(concat_ws(",", {columns}))) FROM {table}').split()
        for table, columns in (('customer', 'id, name, note, hex(photo)'), ('purchase', 'id, customer_id, total'))
    }


@pytest.fixture
def configured(settings, tmp_path, server):
    identity = tmp_path / 'identity.txt'
    subprocess.run(['age-keygen', '-o', str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(['age-keygen', '-y', str(identity)], check=True, capture_output=True, text=True).stdout
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'acme/prod',
        'STORE': {'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore', 'ROOT': str(tmp_path / 'store')},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': [recipient.strip()]},
        'DATABASES': {
            'BACKEND': 'offsite_backup.databases.mysql.MySQLSource',
            'ALIASES': ['my_source'],
            'CLIENT': CLIENT,
        },
        'NOTIFY': [],
    }
    return identity


def test_backup_then_restore_into_an_empty_database(configured):
    """Every row and every byte of every column back, views and foreign keys included."""
    from offsite_backup.pass_ import run
    from offsite_backup.restore import restore_database

    summary = run(reason='manual')
    assert summary.failed == {}
    key = restore_database('latest', 'my_target', identity=configured, source_alias='my_source')
    assert key.startswith('acme/prod/db-my_source/')
    assert snapshot('ob_target') == snapshot('ob_source')
    assert snapshot('ob_target')['customer'][0] == str(ROWS)
    assert (
        sql('ob_target', 'SELECT count(*) FROM big_purchase').strip()
        == sql('ob_source', 'SELECT count(*) FROM big_purchase').strip()
    )


def test_a_restore_refuses_a_database_with_tables_and_clean_replaces_it(configured):
    """--apply without --clean never writes over tables; with it, the target is replaced."""
    from offsite_backup.exceptions import BackupError
    from offsite_backup.pass_ import run
    from offsite_backup.restore import restore_database

    run()
    sql('ob_target', 'CREATE TABLE leftover (id INT)')
    with pytest.raises(BackupError, match='not empty'):
        restore_database('latest', 'my_target', identity=configured, source_alias='my_source')
    restore_database('latest', 'my_target', identity=configured, source_alias='my_source', clean=True)
    assert 'leftover' not in sql('ob_target', 'SHOW TABLES').split()
    assert snapshot('ob_target') == snapshot('ob_source')
