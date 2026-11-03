from datetime import datetime, timedelta, timezone

import pytest

from offsite_backup.exceptions import BackupUnlockedError
from offsite_backup.stores.s3 import S3Store
from offsite_backup.testing import FakeS3, use_fake_s3

NOW = datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake():
    fake = FakeS3(page_size=2, multipart_threshold=1024, clock=lambda: NOW)
    fake.bucket('b')
    with use_fake_s3(fake):
        yield fake


def store(**options):
    return S3Store({'BUCKET': 'b', 'LOCK_DAYS': 13, **options}, label='STORE')


def write(tmp_path, size):
    path = tmp_path / 'object'
    path.write_bytes(b'x' * size)
    return path


def test_every_upload_carries_the_lock(fake, tmp_path):
    """A single PUT ignores the bucket's default, so mode and date are arguments of every upload."""
    wrote = store().put(write(tmp_path, 10), 'p/db/k', now=NOW)
    extra = fake.calls[-1][1]['ExtraArgs']
    assert extra['ObjectLockMode'] == 'COMPLIANCE'
    assert extra['ObjectLockRetainUntilDate'] == NOW + timedelta(days=13)
    assert wrote.locked_until == NOW + timedelta(days=13)


def test_a_multipart_object_without_a_default_rule_fails_the_read_back(fake, tmp_path):
    """Multipart takes its date from the bucket's default; with none, the object is not locked as asked."""
    fake.bucket('b', default_mode=None, default_days=None)
    with pytest.raises(BackupUnlockedError, match='default COMPLIANCE rule'):
        store().put(write(tmp_path, 2048), 'p/media/k', now=NOW)


def test_a_multipart_object_with_a_shorter_default_rule_fails_the_read_back(fake, tmp_path):
    """An object locked for less than asked fails the pass, before rotation runs."""
    fake.bucket('b', default_days=5)
    with pytest.raises(BackupUnlockedError):
        store().put(write(tmp_path, 2048), 'p/media/k', now=NOW)


def test_a_multipart_object_with_the_matching_rule_passes(fake, tmp_path):
    """Bucket default == LOCK_DAYS makes multipart uploads land as asked."""
    assert store().put(write(tmp_path, 2048), 'p/media/k', now=NOW).locked_until is not None


def test_an_object_that_lands_governance_is_refused(fake, tmp_path, monkeypatch):
    """A PUT whose lock arguments were dropped lands GOVERNANCE, which protects nothing; the read-back says so."""
    upload = fake.upload_file
    monkeypatch.setattr(fake, 'upload_file', lambda *args, **kwargs: upload(*args, **{**kwargs, 'ExtraArgs': {}}))
    with pytest.raises(BackupUnlockedError, match='GOVERNANCE'):
        store().put(write(tmp_path, 10), 'p/media/k', now=NOW)


def test_lock_days_zero_uploads_without_a_lock(fake, tmp_path):
    """A bucket without Object Lock works with LOCK_DAYS 0, and nothing claims a lock."""
    fake.bucket('b', lock_enabled=False)
    wrote = store(LOCK_DAYS=0).put(write(tmp_path, 10), 'p/db/k', now=NOW)
    assert wrote.locked_until is None
    assert fake.calls[-1][1]['ExtraArgs'] == {}


def test_versions_reads_every_page_and_every_marker(fake):
    """Pagination everywhere: markers and versions past the first page are all seen."""
    for index in range(3):
        fake.put_object(Bucket='b', Key=f'p/db/{index}', Body=b'x')
    fake.delete_object(Bucket='b', Key='p/db/0')
    found = store().versions('p/db/')
    assert sorted(found) == ['p/db/0', 'p/db/1', 'p/db/2']
    assert sorted((version.marker, version.latest) for version in found['p/db/0']) == [(False, False), (True, True)]


def test_current_skips_keys_behind_a_delete_marker(fake):
    """A key whose newest version is a marker is not a downloadable backup."""
    fake.put_object(Bucket='b', Key='p/db/a', Body=b'x')
    fake.put_object(Bucket='b', Key='p/db/b', Body=b'x')
    fake.delete_object(Bucket='b', Key='p/db/a')
    assert store().current('p/db/') == ['p/db/b']


def test_deploy_check_names_a_bucket_without_object_lock(fake):
    """E005: a bucket created without Object Lock is said before the first night."""
    fake.bucket('b', lock_enabled=False)
    assert [message.id for message in store().deploy_check()] == ['offsite_backup.E005']


def test_deploy_check_names_an_unversioned_bucket(fake):
    """E005 for versioning, separately from the lock."""
    fake.bucket('b', versioned=False)
    assert 'offsite_backup.E005' in [message.id for message in store().deploy_check()]


@pytest.mark.parametrize(('mode', 'days'), [(None, None), ('COMPLIANCE', 7), ('GOVERNANCE', 13)])
def test_deploy_check_names_a_default_rule_that_is_not_lock_days(fake, mode, days):
    """E006: multipart dates come from this rule, so it must be COMPLIANCE LOCK_DAYS."""
    fake.bucket('b', default_mode=mode, default_days=days)
    assert [message.id for message in store().deploy_check()] == ['offsite_backup.E006']


def test_deploy_check_passes_a_correct_bucket(fake):
    """No finding on a versioned, locked bucket with the matching default rule."""
    assert store().deploy_check() == []


def test_deploy_check_says_an_unreachable_bucket(fake):
    """A bucket that cannot be read is E005, not an exception out of manage.py check."""
    assert [message.id for message in S3Store({'BUCKET': 'missing'}).deploy_check()] == ['offsite_backup.E005']


def test_keep_nights_not_above_lock_days_is_e007(settings, fake):
    """Rotation must only ever meet expired locks."""
    settings.OFFSITE_BACKUP = {'RETENTION': {'KEEP_NIGHTS': 13}}
    assert 'offsite_backup.E007' in [message.id for message in store().check()]
    settings.OFFSITE_BACKUP = {'RETENTION': {'KEEP_NIGHTS': 14}}
    assert 'offsite_backup.E007' not in [message.id for message in store().check()]


def test_the_client_uses_only_the_configured_credentials(monkeypatch):
    """No default credential chain: AWS_* in the environment does not decide where backups go."""
    pytest.importorskip('boto3')
    monkeypatch.setenv('AWS_ACCESS_KEY_ID', 'stray')
    client = S3Store(
        {'BUCKET': 'b', 'ACCESS_KEY': 'mine', 'SECRET_KEY': 's', 'ENDPOINT_URL': 'http://127.0.0.1:1', 'REGION': 'x'}
    ).client
    assert client._request_signer._credentials.access_key == 'mine'
