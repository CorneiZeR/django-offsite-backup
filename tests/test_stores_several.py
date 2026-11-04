import json
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command

from offsite_backup import naming
from offsite_backup.check import check_last_night
from offsite_backup.checks import check_settings
from offsite_backup.exceptions import BackupFailedError, BackupMissingError, MisconfiguredError
from offsite_backup.notify import capture
from offsite_backup.pass_ import back_up_database, keep_apart, run
from offsite_backup.restore import fetch, restore_database
from offsite_backup.stores.filesystem import DirectoryStore
from tests import backends
from tests.conftest import BUCKET


def s3_keys(fake):
    return sorted(key for key, held in fake.buckets[BUCKET].keys.items() if held and not held[-1].marker)


def local_keys(root):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file())


@pytest.fixture
def local(tmp_path):
    return tmp_path / 'local-store'


@pytest.fixture
def two_stores(configured, settings, local):
    s3 = {**settings.OFFSITE_BACKUP['STORE'], 'NAME': 'offsite'}
    settings.OFFSITE_BACKUP['STORE'] = [
        s3,
        {'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore', 'ROOT': str(local), 'NAME': 'local'},
    ]
    return settings


def ids(messages):
    return sorted(message.id for message in messages)


def test_every_object_goes_to_every_store(two_stores, fake_s3, now, local):
    """One pass, the same keys in each store."""
    summary = run(now=now)
    assert s3_keys(fake_s3) == local_keys(local)
    assert len(s3_keys(fake_s3)) == 4
    assert summary.objects == 4
    assert sorted({wrote.store for wrote in summary.uploaded}) == ['local', 'offsite']
    assert summary.bytes == sum(wrote.size for wrote in summary.uploaded if wrote.store == 'offsite')


def test_each_store_reads_its_own_lock_back(two_stores, fake_s3, now):
    """The S3 copy is locked and read back; the directory copy, which cannot lock, says none."""
    summary = run(now=now)
    locks = {wrote.store: wrote.locked_until for wrote in summary.uploaded if '/db/' in wrote.key}
    assert locks['offsite'] == now + timedelta(days=13)
    assert locks['local'] is None


def test_a_store_that_refuses_fails_the_pass_by_name_and_the_other_holds_it(
    two_stores, fake_s3, now, settings, tmp_path
):
    """No silent partial: the other store takes every object, the pass fails naming the one that did not."""
    settings.OFFSITE_BACKUP['STORE'][1]['BACKEND'] = 'tests.backends.RefusingStore'
    settings.OFFSITE_BACKUP['LAST_SUCCESS_FILE'] = str(tmp_path / 'last')
    with capture() as said, pytest.raises(BackupFailedError, match=r'1 store\(s\) not written.*local: OSError'):
        run(now=now)
    assert len(s3_keys(fake_s3)) == 4
    [(outcome, text)] = said
    assert outcome.kind == 'failed'
    assert 'local' in text
    assert not (tmp_path / 'last').exists()


def test_a_refusing_store_is_not_written_to_again_nor_rotated(two_stores, fake_s3, now, settings, monkeypatch):
    """Its night is lost; rotating it would make room for a backup it does not hold."""
    settings.OFFSITE_BACKUP['STORE'][1]['BACKEND'] = 'tests.backends.RefusingStore'
    calls = []
    monkeypatch.setattr(backends.RefusingStore, 'put', lambda self, *a, **k: calls.append(a) or 1 / 0)
    monkeypatch.setattr(backends.RefusingStore, 'versions', lambda self, prefix: calls.append(prefix) or {})
    with pytest.raises(BackupFailedError):
        run(now=now)
    assert len(calls) == 1


def test_one_store_alone_fails_as_before(configured, fake_s3, now, settings, tmp_path):
    """A single store's refusal is the pass's own error, not a store report."""
    settings.OFFSITE_BACKUP['STORE'] = {'BACKEND': 'tests.backends.RefusingStore', 'ROOT': str(tmp_path / 's')}
    settings.OFFSITE_BACKUP['DATABASES'] = None
    with pytest.raises(OSError, match='No space left'):
        run(now=now)


def test_every_store_refusing_fails_at_once(two_stores, now, settings, tmp_path):
    """Nothing to carry the pass on: it stops at the first object."""
    for block in settings.OFFSITE_BACKUP['STORE']:
        block.update({'BACKEND': 'tests.backends.RefusingStore', 'ROOT': str(tmp_path / block['NAME'])})
    settings.OFFSITE_BACKUP['DATABASES'] = None
    with pytest.raises(BackupFailedError, match='no store took the backup'):
        run(now=now)


def test_each_store_rotates_by_its_own_keep_nights(two_stores, fake_s3, now, local, settings):
    """A short local window and a long off-site one, from the same passes."""
    settings.OFFSITE_BACKUP['STORE'][0]['LOCK_DAYS'] = 0
    fake_s3.bucket(BUCKET, lock_enabled=False)
    settings.OFFSITE_BACKUP['STORE'][1]['KEEP_NIGHTS'] = 2
    for day in range(4):
        run(now=now + timedelta(days=day), only=['db'], media=True)
        run(now=now + timedelta(days=day))
    store = DirectoryStore({'ROOT': str(local)})
    local_nights = {naming.night(key) for key in store.current('acme/prod/db/')}
    s3_nights = {naming.night(key) for key in s3_keys(fake_s3) if '/db/' in key}
    assert len(local_nights) == 2
    assert len(s3_nights) == 4


def test_several_stores_need_a_name_each(configured, settings, local):
    """--store and the messages name a store; two unnamed ones cannot be told apart."""
    settings.OFFSITE_BACKUP['STORE'] = [
        settings.OFFSITE_BACKUP['STORE'],
        {'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore', 'ROOT': str(local)},
    ]
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E001']
    assert "OFFSITE_BACKUP['STORE'][0]['NAME'] (several stores need a name each)" in error.msg
    with pytest.raises(MisconfiguredError, match='NAME'):
        run()


def test_two_stores_cannot_share_a_name(two_stores, settings):
    """A name is how one store is picked out."""
    settings.OFFSITE_BACKUP['STORE'][1]['NAME'] = 'offsite'
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E001']
    assert "'offsite' is taken by another store" in error.msg


def test_one_store_needs_no_name(configured):
    """The single-store setting is unchanged."""
    assert 'offsite_backup.E001' not in ids(check_settings())


def test_a_store_without_its_backend_is_named(two_stores, settings):
    """E001 names the list entry."""
    settings.OFFSITE_BACKUP['STORE'][1]['BACKEND'] = ''
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E001']
    assert "OFFSITE_BACKUP['STORE'][1]['BACKEND']" in error.msg


def test_a_store_keep_nights_not_above_its_lock_is_e007(two_stores, settings):
    """E007 compares each store's own window with its own lock."""
    settings.OFFSITE_BACKUP['STORE'][0]['KEEP_NIGHTS'] = 10
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E007']
    assert "OFFSITE_BACKUP['STORE'][0]['KEEP_NIGHTS'] is 10" in error.msg


@pytest.mark.parametrize('value', [0, -1, 'two', True])
def test_a_store_keep_nights_must_be_a_positive_integer(two_stores, settings, value):
    """E001 for a window that is not a number of nights."""
    settings.OFFSITE_BACKUP['STORE'][1]['KEEP_NIGHTS'] = value
    assert 'offsite_backup.E001' in ids(check_settings())


def test_restore_reads_the_named_store(two_stores, fake_s3, now, tmp_path, local):
    """--store: the local copy serves a restore while the off-site one is out of reach."""
    run(now=now)
    fake_s3.buckets.clear()
    restored = fetch('latest', into=tmp_path / 'out', identity=__file__, kinds=['db'], store='local')
    assert restored[0].path.read_text().startswith('-- dump of default')
    key = restore_database('latest', 'default', identity=__file__, store='local')
    assert key.startswith('acme/prod/db/')


def test_an_unknown_store_name_is_refused(two_stores, fake_s3, now, tmp_path):
    """A typo is named, with the names there are."""
    with pytest.raises(MisconfiguredError, match=r"no store is named 'nope'.*'offsite', 'local'"):
        fetch('latest', into=tmp_path, identity=__file__, store='nope')


def test_backup_list_lists_the_named_store(two_stores, fake_s3, now):
    """--store picks the store; the JSON says which."""
    run(now=now)
    out = StringIO()
    call_command('backup_list', '--json', '--store', 'local', stdout=out)
    data = json.loads(out.getvalue())
    assert data['store'] == 'local'
    assert {row['kind'] for row in data['kinds']} == {'db', 'media', 'config'}
    assert all(row['newest_locked_until'] is None for row in data['kinds'])


def test_the_morning_check_names_the_store_that_stopped_receiving(two_stores, fake_s3, now, local):
    """A copy that stopped arriving is MISSING by its store's name, the other store notwithstanding."""
    run(now=now)
    for path in local.rglob('*'):
        if path.is_file():
            path.unlink()
    with pytest.raises(BackupMissingError, match='local: config/ \\(none\\)') as raised:
        check_last_night(now=now + timedelta(hours=6))
    assert 'offsite:' not in str(raised.value)


def test_a_store_without_any_database_backup_is_missing_not_new(two_stores, fake_s3, now, local, settings):
    """A store that never got a database is not excused as a new tenant by another store's backups."""
    settings.OFFSITE_BACKUP['CONFIG'] = None
    run(now=now)
    for path in (local / 'acme' / 'prod' / 'db').rglob('*'):
        path.unlink()
    with pytest.raises(BackupMissingError, match='local: db/'):
        check_last_night(now=now + timedelta(hours=6))


def test_the_message_names_the_stores(two_stores, fake_s3, now):
    """Several stores are said; one store's message is unchanged."""
    with capture() as said:
        run(now=now)
    assert '🗃 Stores: offsite, local' in said[0][1]
    assert '📦 Objects: 4' in said[0][1]


def test_backup_offsite_says_how_many_stores(two_stores, fake_s3):
    """The deploy log counts objects once, and the stores they went to."""
    out = StringIO()
    call_command('backup_offsite', '--without-media', '--reason', 'deploy', stdout=out)
    assert out.getvalue().startswith('backup_offsite: 2 objects') and 'to each of 2 stores' in out.getvalue()


def test_back_up_database_and_keep_apart_reach_every_store(two_stores, fake_s3, now, local):
    """The one-off dump before a removal is in each store, and so is its released copy."""
    key = back_up_database('gone', now=now)
    copy = keep_apart(key, now=now)
    for keys in (s3_keys(fake_s3), local_keys(local)):
        assert key in keys
        assert copy in keys


def test_the_database_guard_is_not_done_until_every_store_is(two_stores, fake_s3, now, settings, monkeypatch, local):
    """Tonight in one store only is not a done night: the retry writes both."""
    import contextlib

    from offsite_backup import guard

    @contextlib.contextmanager
    def lock(alias, key):
        yield True

    monkeypatch.setattr(guard, '_advisory_lock', lock)
    settings.OFFSITE_BACKUP['GUARD'] = {'BACKEND': 'database'}
    assert guard.run_nightly(now=now) is not None
    assert guard.run_nightly(now=now + timedelta(minutes=5)) is None
    for path in (local / 'acme' / 'prod' / 'config').rglob('*'):
        path.unlink()
    assert guard.run_nightly(now=now + timedelta(minutes=10)) is not None
