"""Against real services: PostgreSQL, an S3 server with Object Lock, ``age`` and ``zstd``.

Run with ``OFFSITE_BACKUP_TEST_S3_ENDPOINT`` set and ``--ds=tests.integration_settings``;
skipped otherwise. Each run uses fresh buckets, because COMPLIANCE locks cannot be lifted.
"""

import os
import secrets
import shutil
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.integration

ENDPOINT = os.environ.get('OFFSITE_BACKUP_TEST_S3_ENDPOINT', '')
ACCESS = os.environ.get('OFFSITE_BACKUP_TEST_S3_ACCESS_KEY', 'minio')
SECRET = os.environ.get('OFFSITE_BACKUP_TEST_S3_SECRET_KEY', 'minio12345')

if not ENDPOINT:
    pytest.skip('OFFSITE_BACKUP_TEST_S3_ENDPOINT is not set', allow_module_level=True)
for binary in ('age', 'age-keygen', 'zstd', 'pg_dump', 'psql', 'pg_restore'):
    if shutil.which(binary) is None:
        pytest.skip(f'{binary} is not installed', allow_module_level=True)

boto3 = pytest.importorskip('boto3')

LOCK_DAYS = 1
TABLES = {
    'shop_customer': 'id serial primary key, name text, note text',
    'shop_order': 'id serial primary key, customer_id int references shop_customer(id), total numeric(12,2)',
    'empty_table': 'id serial primary key',
}


def client():
    return boto3.client(
        's3', endpoint_url=ENDPOINT, aws_access_key_id=ACCESS, aws_secret_access_key=SECRET, region_name='us-east-1'
    )


@pytest.fixture
def bucket():
    name = f'ob-{secrets.token_hex(6)}'
    s3 = client()
    s3.create_bucket(Bucket=name, ObjectLockEnabledForBucket=True)
    s3.put_object_lock_configuration(
        Bucket=name,
        ObjectLockConfiguration={
            'ObjectLockEnabled': 'Enabled',
            'Rule': {'DefaultRetention': {'Mode': 'COMPLIANCE', 'Days': LOCK_DAYS}},
        },
    )
    return name


@pytest.fixture
def plain_bucket():
    name = f'ob-plain-{secrets.token_hex(6)}'
    s3 = client()
    s3.create_bucket(Bucket=name)
    s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={'Status': 'Enabled'})
    return name


@pytest.fixture
def keys(tmp_path):
    identity = tmp_path / 'identity.txt'
    subprocess.run(['age-keygen', '-o', str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(['age-keygen', '-y', str(identity)], check=True, capture_output=True, text=True).stdout
    return identity, recipient.strip()


@pytest.fixture
def databases(django_db_blocker):
    from django.db import connections

    with django_db_blocker.unblock():
        for alias in ('default', 'target'):
            with connections[alias].cursor() as cursor:
                cursor.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
        with connections['default'].cursor() as cursor:
            for table, columns in TABLES.items():
                cursor.execute(f'CREATE TABLE {table} ({columns})')
            cursor.execute(
                'INSERT INTO shop_customer (name, note) '
                "SELECT 'customer ' || n, repeat('ünïcødé ', n % 50) FROM generate_series(1, 2500) AS n"
            )
            cursor.execute(
                'INSERT INTO shop_order (customer_id, total) '
                'SELECT 1 + n % 2500, n * 1.25 FROM generate_series(1, 7000) AS n'
            )
        yield connections
        for alias in ('default', 'target'):
            connections[alias].close()


def counts(connections, alias):
    with connections[alias].cursor() as cursor:
        found = {}
        for table in TABLES:
            cursor.execute(f'SELECT count(*) FROM {table}')
            found[table] = cursor.fetchone()[0]
        return found


@pytest.fixture
def configure(settings, tmp_path, keys):
    media = tmp_path / 'media'
    (media / 'photos').mkdir(parents=True)
    (media / 'photos' / 'one.jpg').write_bytes(os.urandom(200_000))
    (media / 'big.bin').write_bytes(os.urandom(70 * 1024 * 1024))  # above one 64 MiB part: multipart
    deploy = tmp_path / 'deploy'
    deploy.mkdir()
    (deploy / 'compose.yml').write_text('services: {}\n')

    def apply(bucket, *, fmt='plain', lock_days=LOCK_DAYS):
        settings.OFFSITE_BACKUP = {
            'PREFIX': 'acme/prod',
            'ENV': 'test',
            'STORE': {
                'BACKEND': 'offsite_backup.stores.s3.S3Store',
                'ENDPOINT_URL': ENDPOINT,
                'REGION': 'us-east-1',
                'BUCKET': bucket,
                'ACCESS_KEY': ACCESS,
                'SECRET_KEY': SECRET,
                'LOCK_DAYS': lock_days,
            },
            'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': [keys[1]]},
            'DATABASES': {'BACKEND': 'offsite_backup.databases.postgres.PostgresSource', 'FORMAT': fmt},
            'MEDIA': [{'BACKEND': 'offsite_backup.media.filesystem.DirectorySource', 'ROOT': str(media)}],
            'CONFIG': {'BACKEND': 'offsite_backup.config.paths.PathsSource', 'PATHS': [str(deploy)]},
            'RETENTION': {'KEEP_NIGHTS': lock_days + 1},
            'NOTIFY': [],
        }
        return media

    return apply


@pytest.mark.parametrize('fmt', ['plain', 'custom'])
def test_backup_then_restore_into_a_clean_database(configure, bucket, databases, keys, django_db_blocker, fmt):
    """The whole cycle: every table's row count matches after a restore into an empty database."""
    from offsite_backup.pass_ import run
    from offsite_backup.restore import restore_database

    configure(bucket, fmt=fmt)
    with django_db_blocker.unblock():
        summary = run(reason='manual')
        assert summary.failed == {}
        key = restore_database('latest', 'target', identity=keys[0], source_alias='default')
        assert key.endswith('.sql.zst.age' if fmt == 'plain' else '.dump.zst.age')
        assert (
            counts(databases, 'target')
            == counts(databases, 'default')
            == {
                'shop_customer': 2500,
                'shop_order': 7000,
                'empty_table': 0,
            }
        )


def test_every_object_is_locked_compliance_including_multipart(configure, bucket, databases, django_db_blocker):
    """The read-back passed, and the bucket agrees: COMPLIANCE on every object."""
    from offsite_backup.pass_ import run

    configure(bucket)
    with django_db_blocker.unblock():
        summary = run()
    s3 = client()
    for wrote in summary.uploaded:
        head = s3.head_object(Bucket=bucket, Key=wrote.key)
        assert head['ObjectLockMode'] == 'COMPLIANCE'
        assert head['ObjectLockRetainUntilDate'] > datetime.now(timezone.utc) + timedelta(hours=LOCK_DAYS * 24 - 2)


def test_media_round_trips(configure, bucket, databases, keys, django_db_blocker, tmp_path):
    """Every file comes back byte for byte from the volumes."""
    from offsite_backup.pass_ import run
    from offsite_backup.restore import fetch

    media = configure(bucket)
    with django_db_blocker.unblock():
        summary = run()
    restored = fetch(summary.stamp, into=tmp_path / 'out', identity=keys[0], kinds=['media'])
    out = tmp_path / 'extracted'
    for item in restored:
        with tarfile.open(item.path) as tar:
            tar.extractall(out, filter='data')
    for path in media.rglob('*'):
        if path.is_file():
            assert (out / path.relative_to(media)).read_bytes() == path.read_bytes()


def test_a_restore_refuses_a_non_empty_database(configure, bucket, databases, keys, django_db_blocker):
    """--apply without --clean never writes over a database that has tables."""
    from offsite_backup.exceptions import BackupError
    from offsite_backup.pass_ import run
    from offsite_backup.restore import restore_database

    configure(bucket)
    with django_db_blocker.unblock():
        run(only=['db'])
        with pytest.raises(BackupError, match='not empty'):
            restore_database('latest', 'default', identity=keys[0])


def test_deploy_check_on_real_buckets(configure, bucket, plain_bucket):
    """A correct bucket has no finding; one without Object Lock is E005."""
    from offsite_backup.checks import check_deploy

    configure(bucket)
    assert [message.id for message in check_deploy() if message.id != 'offsite_backup.E004'] == []
    configure(plain_bucket)
    assert 'offsite_backup.E005' in [message.id for message in check_deploy()]


def test_rotation_against_real_versions_and_markers(configure, plain_bucket):
    """Every version and delete marker of an expired night goes; newer nights stay."""
    from offsite_backup.rotation import rotate
    from offsite_backup.stores.s3 import S3Store

    configure(plain_bucket, lock_days=0)
    s3 = client()
    for day in range(1, 6):
        key = f'acme/prod/db/2026-01-{day:02d}T03-00Z.sql.zst.age'
        s3.put_object(Bucket=plain_bucket, Key=key, Body=b'v1')
        s3.put_object(Bucket=plain_bucket, Key=key, Body=b'v2')
    s3.delete_object(Bucket=plain_bucket, Key='acme/prod/db/2026-01-01T03-00Z.sql.zst.age')
    store = S3Store(
        {'BUCKET': plain_bucket, 'ENDPOINT_URL': ENDPOINT, 'ACCESS_KEY': ACCESS, 'SECRET_KEY': SECRET, 'LOCK_DAYS': 0}
    )
    result = rotate(store, 'acme/prod/db/', 2)
    assert len(result.removed) == 3
    left = s3.list_object_versions(Bucket=plain_bucket, Prefix='acme/prod/db/')
    assert {item['Key'] for item in left.get('Versions', [])} == {
        'acme/prod/db/2026-01-04T03-00Z.sql.zst.age',
        'acme/prod/db/2026-01-05T03-00Z.sql.zst.age',
    }
    assert not left.get('DeleteMarkers')


def test_pg_dump_is_new_enough(configure, bucket, django_db_blocker):
    """E004 stays silent when the client is at least the server's major."""
    from offsite_backup.checks import check_deploy

    configure(bucket)
    with django_db_blocker.unblock():
        assert 'offsite_backup.E004' not in [message.id for message in check_deploy()]
