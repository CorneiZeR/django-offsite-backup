import io
import tarfile
from datetime import datetime, timedelta, timezone

import pytest

from offsite_backup import naming
from offsite_backup.exceptions import BackupFailedError, MisconfiguredError, NotConfiguredError
from offsite_backup.pass_ import back_up_database, keep_apart, run
from offsite_backup.sealing import Sealing
from offsite_backup.testing import Passthrough, capture
from tests import backends
from tests.conftest import BUCKET


def stored(fake):
    return sorted(key for key, held in fake.buckets[BUCKET].keys.items() if held and not held[-1].marker)


def body(fake, key):
    return fake.buckets[BUCKET].keys[key][-1].data


def uploads(fake):
    return [call[1]['Key'] for call in fake.calls if call[0] == 'upload_file']


def test_a_pass_writes_every_part_under_the_prefix(configured, fake_s3, now):
    """Database, media volumes and config, each under its kind."""
    summary = run(now=now)
    stamp = naming.stamp(now)
    assert stored(fake_s3) == [
        f'acme/prod/config/{stamp}.tar.zst.age',
        f'acme/prod/db/{stamp}.sql.zst.age',
        f'acme/prod/media/{stamp}.001.tar.age',
    ]
    assert summary.bytes == sum(len(body(fake_s3, key)) for key in stored(fake_s3))


def test_the_config_archive_goes_up_last(configured, fake_s3, now):
    """A config object for a stamp means every part before it went up."""
    run(now=now)
    assert uploads(fake_s3)[-1].startswith('acme/prod/config/')


def test_rotation_runs_only_after_every_upload(configured, fake_s3, now, settings):
    """Making room before the new backup exists is how a bad night becomes an empty bucket."""
    fake_s3.bucket(BUCKET, lock_enabled=False)
    settings.OFFSITE_BACKUP['STORE']['LOCK_DAYS'] = 0
    for day in range(1, 20):
        moment = now - timedelta(days=day)
        fake_s3.put_object(Bucket=BUCKET, Key=f'acme/prod/db/{naming.stamp(moment)}.sql.zst.age', Body=b'old')
    fake_s3.calls.clear()
    run(now=now)
    names = [call[0] for call in fake_s3.calls]
    assert names.index('delete_object') > max(index for index, name in enumerate(names) if name == 'upload_file')


def test_media_volumes_are_whole_tars(configured, fake_s3, now, settings):
    """Each volume restores on its own, with the files' relative paths."""
    settings.OFFSITE_BACKUP['MEDIA'][0]['VOLUME_BYTES'] = 20 * 1024 + 2 * (300 + 2048)
    run(now=now)
    volumes = [key for key in stored(fake_s3) if '/media/' in key]
    assert len(volumes) == 2
    names = []
    for key in volumes:
        with tarfile.open(fileobj=io.BytesIO(body(fake_s3, key))) as tar:
            names += tar.getnames()
    assert sorted(names) == ['a.jpg', 'sub/b.png', 'thumbs/t.jpg']


def test_without_media_marks_every_object_and_skips_media(configured, fake_s3, now):
    """A deploy's pass: no media, and marked so it never counts as a night."""
    summary = run(media=False, reason='deploy', now=now)
    keys = stored(fake_s3)
    assert not any('/media/' in key for key in keys)
    assert keys and all(naming.is_partial(key) for key in keys)
    assert not summary.media


def test_without_media_on_a_project_with_no_media_is_whole(configured, fake_s3, now, settings):
    """Nothing is left out when there is no media to leave out."""
    settings.OFFSITE_BACKUP['MEDIA'] = []
    run(media=False, now=now)
    assert not any(naming.is_partial(key) for key in stored(fake_s3))


def test_one_failing_database_does_not_cost_the_others(configured, fake_s3, now, settings):
    """The pass goes on, then fails naming every database that did not go up."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default', 'broken', 'other']
    settings.OFFSITE_BACKUP['DATABASES']['FAIL'] = ['broken']
    with capture() as said, pytest.raises(BackupFailedError, match='broken'):
        run(now=now)
    keys = stored(fake_s3)
    assert any('/db/' in key for key in keys)
    assert any('/db-other/' in key for key in keys)
    assert not any('/db-broken/' in key for key in keys)
    assert any('/media/' in key for key in keys)
    assert any('/config/' in key for key in keys)
    assert [outcome.kind for outcome, _ in said] == ['failed']


def test_a_failure_is_said_and_raised(configured, fake_s3, now, settings):
    """A sealer that dies fails the pass, and the message says why."""
    settings.OFFSITE_BACKUP['COMPRESS'] = ['sh', '-c', 'cat >/dev/null; echo "<oops>" >&2; exit 5']
    settings.OFFSITE_BACKUP['DATABASES'] = None
    with capture() as said, pytest.raises(Exception, match='exited with status 5'):
        run(now=now)
    assert said[0][0].kind == 'failed'
    assert '&lt;oops&gt;' in said[0][1]


def test_a_written_backup_is_said(configured, fake_s3, now):
    """One message per pass, through the configured notifiers."""
    run(now=now)
    assert [kind for kind, _ in backends.Recorder.sent] == ['written']


def test_a_notifier_outage_does_not_fail_the_backup(configured, fake_s3, now, settings):
    """A chat that is down never turns a written backup into a failed one."""
    settings.OFFSITE_BACKUP['NOTIFY'] = [
        {'BACKEND': 'tests.backends.Exploding'},
        {'BACKEND': 'tests.backends.Recorder'},
    ]
    run(now=now)
    assert [kind for kind, _ in backends.Recorder.sent] == ['written']


def test_only_db_writes_only_the_database_and_marks_it(configured, fake_s3, now):
    """``only`` limits the parts; leaving media out marks the pass partial."""
    run(only=['db'], now=now)
    keys = stored(fake_s3)
    assert len(keys) == 1
    assert '/db/' in keys[0]
    assert naming.is_partial(keys[0])


def test_only_refuses_an_unknown_part(configured):
    """A typo is refused, not read as "nothing"."""
    with pytest.raises(ValueError, match='only='):
        run(only=['dbs'])


def test_the_last_success_marker_is_written(configured, fake_s3, now, settings, tmp_path):
    """A monitor reads the moment of the last whole success."""
    settings.OFFSITE_BACKUP['LAST_SUCCESS_FILE'] = str(tmp_path / 'last')
    run(now=now)
    assert (tmp_path / 'last').read_text().strip() == now.isoformat()


def test_not_configured_refuses(settings):
    """A pass asked for on a machine without backups is refused."""
    settings.OFFSITE_BACKUP = {}
    with pytest.raises(NotConfiguredError):
        run()


def test_back_up_database_uploads_one_dump(configured, fake_s3, now):
    """One database now, under its own kind, nothing else."""
    key = back_up_database('tenant7', now=now)
    assert key == f'acme/prod/db-tenant7/{naming.stamp(now)}.sql.zst.age'
    assert stored(fake_s3) == [key]


def test_back_up_database_seals_to_extra_recipients(configured, fake_s3, now, monkeypatch):
    """A one-off dump can be sealed to an extra key as well as the configured ones."""
    seen = []
    monkeypatch.setattr(Passthrough, 'pipeline', lambda self, extra=(): seen.append(tuple(extra)) or ['cat'])
    back_up_database('default', extra_recipients=['age1wake'], now=now)
    assert seen == [('age1wake',)]


def test_back_up_database_keep_apart_writes_under_released(configured, fake_s3, now):
    """keep_apart puts it where no rotation reads."""
    key = back_up_database('gone', keep_apart=True, now=now)
    assert key.startswith('acme/prod/released/db-gone/')


def test_back_up_database_without_databases_configured_refuses(configured, settings):
    """A removal that cannot be backed up does not happen."""
    settings.OFFSITE_BACKUP['DATABASES'] = None
    with pytest.raises(MisconfiguredError):
        back_up_database('default')


def test_keep_apart_copies_under_released(configured, fake_s3, now):
    """The original stays for its rotation; the copy is the one nothing rotates."""
    key = back_up_database('t1', now=now)
    copy = keep_apart(key, now=now)
    assert copy == key.replace('acme/prod/', 'acme/prod/released/')
    assert key in stored(fake_s3)
    assert copy in stored(fake_s3)
    assert keep_apart(copy) == copy


def test_keep_apart_refuses_a_key_outside_the_prefix(configured):
    """Only this prefix's backups."""
    with pytest.raises(ValueError, match='not under'):
        keep_apart('other/prod/db/x')


def test_compression_runs_before_encryption():
    """An encrypted stream compresses by nothing, so the order is fixed."""
    stages = Sealing(Passthrough({}), ['zstd', '-q']).stages()
    assert stages == [['zstd', '-q'], ['cat']]
    assert Sealing(Passthrough({}), ['zstd']).stages(compress=False) == [['cat']]


def test_no_volume_is_larger_than_volume_bytes(configured, fake_s3, now, settings, media_root):
    """Packing budgets tar's own bytes, so a volume never passes VOLUME_BYTES (and one PUT takes it)."""
    for index in range(20):
        (media_root / f'long-name-{"x" * 120}-{index}.bin').write_bytes(b'z' * 3000)
    limit = 60_000
    fake_s3.put_limit = limit
    fake_s3.multipart_threshold = 10**9
    settings.OFFSITE_BACKUP['MEDIA'][0]['VOLUME_BYTES'] = limit
    run(now=now)
    sizes = [len(body(fake_s3, key)) for key in stored(fake_s3) if '/media/' in key]
    assert len(sizes) > 1
    assert max(sizes) <= limit


def test_stamp_comes_from_now(configured, fake_s3):
    """Without ``now``, the stamp is the current UTC minute."""
    summary = run()
    assert abs(naming.parse(summary.keys[0]).when - datetime.now(timezone.utc)) < timedelta(minutes=2)


def test_targets_that_cannot_be_listed_do_not_cost_media_and_config(configured, fake_s3, now, settings):
    """A computed alias list that raises fails the pass at the end, after everything else went up."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = None
    settings.OFFSITE_BACKUP['DATABASES']['BACKEND'] = 'offsite_backup.databases.postgres.PostgresSource'
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = 'tests.test_pass.no_aliases'
    with pytest.raises(BackupFailedError, match='targets'):
        run(now=now)
    assert any('/media/' in key for key in stored(fake_s3))
    assert any('/config/' in key for key in stored(fake_s3))


def no_aliases():
    raise RuntimeError('tenant registry unreachable')
