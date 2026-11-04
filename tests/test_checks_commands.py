import json
import os
import subprocess
import sys
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from offsite_backup import naming
from offsite_backup.checks import check_deploy, check_settings
from offsite_backup.encryption.age_cli import AgeCli
from offsite_backup.restore import fetch, restore_database
from offsite_backup.stores.filesystem import DirectoryStore
from tests import backends
from tests.conftest import BUCKET


def ids(messages):
    return sorted(message.id for message in messages)


# -- system checks -------------------------------------------------------------------------


def test_no_checks_when_off(settings):
    """A machine without backups is not nagged."""
    settings.OFFSITE_BACKUP = {}
    assert check_settings() == []
    assert check_deploy() == []


def test_a_complete_configuration_has_only_the_expected_warnings(configured):
    """LocMemCache is not shared (W003) and the test sealer encrypts nothing (W002); nothing else."""
    assert ids(check_settings()) == ['offsite_backup.W002', 'offsite_backup.W003']


def test_partly_configured_is_e001(configured, settings):
    """E001 lists the missing key by name."""
    settings.OFFSITE_BACKUP['STORE']['SECRET_KEY'] = ''
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E001']
    assert "OFFSITE_BACKUP['STORE']['SECRET_KEY']" in error.msg


def test_a_backend_that_does_not_import_is_e002(configured, settings):
    """A typo in a dotted path is named."""
    settings.OFFSITE_BACKUP['MEDIA'] = [{'BACKEND': 'offsite_backup.media.nope.Source'}]
    assert 'offsite_backup.E002' in ids(check_settings())


def test_a_missing_extra_is_e002_with_the_install_line(configured, monkeypatch):
    """The check names the extra even where the import would fail."""
    import importlib.util

    from offsite_backup.stores import s3

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *a: None if name == 'boto3' else real(name, *a))
    installed, s3.CLIENT_FACTORY = s3.CLIENT_FACTORY, None
    try:
        [error] = [message for message in check_settings() if message.id == 'offsite_backup.E002']
    finally:
        s3.CLIENT_FACTORY = installed
    assert error.hint == 'pip install "django-offsite-backup[s3]"'


def test_a_missing_binary_is_e003(configured, settings):
    """zstd absent while COMPRESS runs it."""
    settings.OFFSITE_BACKUP['COMPRESS'] = ['zstd']
    os.environ['PATH'], saved = '/nonexistent', os.environ['PATH']
    try:
        found = ids(check_settings())
    finally:
        os.environ['PATH'] = saved
    assert 'offsite_backup.E003' in found


def test_keep_not_above_lock_is_e007(configured, settings):
    """Rotation must never meet a locked object."""
    settings.OFFSITE_BACKUP['RETENTION'] = {'KEEP_NIGHTS': 13}
    assert 'offsite_backup.E007' in ids(check_settings())


def test_a_private_key_as_recipient_is_e008():
    """The machine must hold public keys only."""
    assert [
        found
        for found in ids(AgeCli({'RECIPIENTS': ['AGE-SECRET-KEY-1XYZ']}).check())
        if found != 'offsite_backup.E003'
    ] == ['offsite_backup.E008']


def test_a_directory_store_warns_w001(configured, settings, tmp_path):
    """No immutability, kept on the machine it protects."""
    settings.OFFSITE_BACKUP['STORE'] = {
        'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore',
        'ROOT': str(tmp_path),
    }
    assert 'offsite_backup.W001' in ids(check_settings())


def test_a_shared_cache_has_no_w003(configured, settings, tmp_path):
    """A cache workers share satisfies the guard."""
    settings.CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': str(tmp_path)}
    }
    assert 'offsite_backup.W003' not in ids(check_settings())


def test_notifiers_in_an_unsilenced_test_run_are_w004(configured, monkeypatch):
    """A test run with notifiers configured and not silenced is warned."""
    monkeypatch.setattr('offsite_backup.notify._state', {'silenced': 0, 'captures': []})
    assert 'offsite_backup.W004' in ids(check_settings())


def test_deploy_checks_reach_the_bucket(configured, fake_s3):
    """E005/E006 run as deploy checks."""
    fake_s3.bucket(BUCKET, lock_enabled=False)
    assert ids(check_deploy()) == ['offsite_backup.E005']


def manage_check(**options):
    out, err = StringIO(), StringIO()
    call_command('check', stdout=out, stderr=err, **options)
    return out.getvalue() + err.getvalue()


def test_manage_check_without_the_config_mount_prints_no_w005(configured, settings, tmp_path):
    """A web or migrations container without the deployment mount is not warned; check --deploy is."""
    settings.OFFSITE_BACKUP['CONFIG']['PATHS'] = [str(tmp_path / 'not-mounted')]
    assert 'offsite_backup.W005' not in manage_check()
    assert 'offsite_backup.W005' in manage_check(deploy=True)


def test_manage_check_without_the_media_mount_prints_no_w005(configured, settings, tmp_path):
    """The same for a media directory that is not mounted here."""
    settings.OFFSITE_BACKUP['MEDIA'][0]['ROOT'] = str(tmp_path / 'not-mounted')
    assert 'offsite_backup.W005' not in manage_check()
    assert 'offsite_backup.W005' in manage_check(deploy=True)


def test_plain_manage_check_does_not_reach_the_network():
    """Network checks are registered with deploy=True only."""
    from django.core.checks.registry import registry

    assert check_deploy in registry.deployment_checks
    assert check_deploy not in registry.registered_checks


# -- commands ------------------------------------------------------------------------------


def command(*args):
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


@pytest.mark.parametrize('name', ['backup_offsite', 'backup_check', 'backup_list'])
def test_commands_say_nothing_to_do_when_off(settings, name):
    """Development machines exit 0."""
    settings.OFFSITE_BACKUP = {}
    assert 'nothing to do' in command(name)


def test_backup_offsite_prints_what_went_up(configured, fake_s3):
    """The deploy log shows objects and bytes."""
    assert command('backup_offsite', '--reason', 'deploy', '--without-media').startswith('backup_offsite: 2 objects')


def test_backup_offsite_fails_non_zero(configured, fake_s3, settings):
    """A failure is a CommandError, so the deploy stops."""
    settings.OFFSITE_BACKUP['DATABASES']['FAIL'] = ['default']
    with pytest.raises(CommandError, match='not backed up'):
        command('backup_offsite')


def test_backup_offsite_refuses_an_empty_reason(configured):
    """The reason is the message's second line."""
    with pytest.raises(CommandError, match='reason'):
        command('backup_offsite', '--reason', ' ')


def test_backup_check_fails_when_missing(configured, fake_s3):
    """The scheduler's failure mail fires too."""
    with pytest.raises(CommandError, match='No whole backup'):
        command('backup_check')


def test_backup_list_reports_nights_per_kind(configured, fake_s3, now):
    """Kinds, nights and partial passes, as JSON."""
    command('backup_offsite')
    command('backup_offsite', '--without-media')
    data = json.loads(command('backup_list', '--json'))
    kinds = {row['kind']: row for row in data['kinds']}
    assert set(kinds) == {'db', 'media', 'config'}
    assert kinds['db']['nights'] == 1
    assert kinds['db']['partial_objects'] == 1
    assert kinds['db']['newest_locked_until']


# -- restore -------------------------------------------------------------------------------


def test_fetch_brings_every_part_back_and_verifies_the_dump(configured, fake_s3, tmp_path, monkeypatch, now):
    """Download, decrypt, verify, into one directory per kind."""
    from offsite_backup.pass_ import run

    run(now=now)
    identity = tmp_path / 'id.txt'
    identity.write_text('AGE-SECRET-KEY-1')
    restored = fetch('latest', into=tmp_path / 'out', identity=identity)
    assert sorted(item.kind for item in restored) == ['config', 'db', 'media']
    db = next(item for item in restored if item.kind == 'db')
    assert db.verified
    assert db.path.read_text().startswith('-- dump of default')
    assert db.path.name.endswith('.sql')


def test_latest_ignores_a_pass_without_media(configured, fake_s3, tmp_path, now):
    """'latest' is the newest whole backup."""
    from offsite_backup.pass_ import run

    run(now=now)
    run(media=False, now=now + timedelta(hours=5))
    restored = fetch('latest', into=tmp_path / 'out', identity=__file__)
    assert {naming.parse(item.key).stamp for item in restored} == {naming.stamp(now)}


def test_the_identity_comes_from_the_environment(configured, fake_s3, tmp_path, monkeypatch, now):
    """AGE_IDENTITY, never an argument."""
    from offsite_backup.pass_ import run

    run(now=now)
    monkeypatch.setenv('AGE_IDENTITY', 'AGE-SECRET-KEY-1')
    assert fetch(naming.stamp(now), into=tmp_path / 'out', kinds=['db'])


def test_no_identity_is_refused(configured, fake_s3, tmp_path, monkeypatch, now):
    """Without a key there is nothing to decrypt with."""
    from offsite_backup.exceptions import MisconfiguredError
    from offsite_backup.pass_ import run

    run(now=now)
    monkeypatch.delenv('AGE_IDENTITY', raising=False)
    with pytest.raises(MisconfiguredError, match='AGE_IDENTITY'):
        fetch('latest', into=tmp_path / 'out')


def test_restore_database_loads_the_dump(configured, fake_s3, now):
    """--apply: verified, then handed to the source with clean as asked."""
    from offsite_backup.pass_ import run

    run(now=now)
    key = restore_database('latest', 'default', identity=__file__, clean=True)
    assert key.startswith('acme/prod/db/')
    assert backends.RESTORED == [('-- dump of default\n-- PostgreSQL database dump complete\n', 'default', True)]


def test_backup_restore_apply_needs_a_database(configured):
    """--apply without --database is refused."""
    with pytest.raises(CommandError, match='--database'):
        command('backup_restore', '--stamp', 'latest', '--apply')


def test_backup_restore_clean_without_apply_is_refused(configured):
    """--clean alone means nothing and says so."""
    with pytest.raises(CommandError, match='--clean'):
        command('backup_restore', '--stamp', 'latest', '--clean')


# -- directory store -----------------------------------------------------------------------


def test_directory_store_writes_atomically_and_privately(tmp_path):
    """0600 files, 0700 directories, no .part left behind."""
    store = DirectoryStore({'ROOT': str(tmp_path / 'store')})
    source = tmp_path / 'x'
    source.write_text('data')
    store.put(source, 'p/db/2026-01-01T03-00Z.sql')
    target = tmp_path / 'store' / 'p' / 'db' / '2026-01-01T03-00Z.sql'
    assert target.read_text() == 'data'
    assert oct(target.stat().st_mode & 0o777) == '0o600'
    assert oct(target.parent.stat().st_mode & 0o777) == '0o700'
    assert not list(target.parent.glob('*.part'))
    assert store.current('p/db/') == ['p/db/2026-01-01T03-00Z.sql']


def test_directory_store_refuses_a_key_leaving_its_root(tmp_path):
    """A key cannot climb out of ROOT."""
    with pytest.raises(ValueError, match='leaves'):
        DirectoryStore({'ROOT': str(tmp_path)}).get('../../etc/passwd', tmp_path / 'x')


# -- imports -------------------------------------------------------------------------------

BLOCKER = """
import importlib, pkgutil, sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in {'boto3', 'botocore', 'httpx2', 'django_aiogram', 'aiogram', 'celery', 'taskiq'}:
            raise ImportError(f'blocked {name}')
sys.meta_path.insert(0, Block())
import django
from django.conf import settings
settings.configure(INSTALLED_APPS=['offsite_backup'])
django.setup()
import offsite_backup
for module in pkgutil.walk_packages(offsite_backup.__path__, 'offsite_backup.'):
    if module.name.startswith('offsite_backup.schedule.') or module.name.endswith('pytest_plugin'):
        continue
    importlib.import_module(module.name)
print('ok')
"""


def test_every_module_imports_without_any_extra():
    """Each backend imports its driver lazily, so the base install imports everything."""
    result = subprocess.run([sys.executable, '-c', BLOCKER], capture_output=True, text=True, check=False)
    assert result.stdout.strip() == 'ok', result.stderr


def test_importing_the_package_loads_no_driver():
    """``import offsite_backup`` stays cheap."""
    code = (
        'import sys, offsite_backup; '
        "print(sorted(m for m in sys.modules if m.split('.')[0] in {'boto3', 'httpx2', 'django_aiogram', 'offsite_backup'}))"
    )
    out = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "['offsite_backup']"


def test_wheel_marks_itself_typed():
    """py.typed ships beside the package."""
    import offsite_backup

    assert (Path(offsite_backup.__file__).parent / 'py.typed').is_file()


def test_restore_latest_of_one_database_counts_a_deploy_pass(configured, fake_s3, now):
    """A deploy's dump is a whole database: 'latest' for one database is the newest dump of it."""
    from offsite_backup.pass_ import run

    run(now=now)
    run(media=False, reason='deploy', now=now + timedelta(hours=5))
    key = restore_database('latest', 'scratch', identity=__file__, source_alias='default')
    assert naming.is_partial(key)
    assert backends.RESTORED[0][1] == 'scratch'


def test_volume_bytes_at_or_below_the_tar_overhead_is_refused():
    """A volume budget smaller than tar's own bytes could never hold a file."""
    from offsite_backup.media.base import VOLUME_OVERHEAD
    from offsite_backup.media.filesystem import DirectorySource

    found = DirectorySource({'ROOT': '/', 'VOLUME_BYTES': VOLUME_OVERHEAD}, label='MEDIA').check()
    assert 'offsite_backup.E001' in ids(found)


def test_a_notifier_missing_its_token_is_e001(configured, settings):
    """A notifier's required options are named like any other backend's."""
    settings.OFFSITE_BACKUP['NOTIFY'] = [
        {'BACKEND': 'offsite_backup.notifiers.telegram.TelegramNotifier', 'CHAT_ID': 1}
    ]
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E001']
    assert "OFFSITE_BACKUP['NOTIFY'][0]['TOKEN']" in error.msg


def test_a_public_recipient_alone_leaves_backups_off(settings):
    """A recipient written into settings does not turn backups on where no store key is."""
    from offsite_backup import conf

    settings.OFFSITE_BACKUP = {
        'PREFIX': 'p',
        'STORE': {'BACKEND': 'offsite_backup.stores.s3.S3Store', 'BUCKET': '', 'ACCESS_KEY': '', 'SECRET_KEY': ''},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': ['age1public']},
    }
    assert not conf.enabled()


def test_a_custom_compressor_is_checked_by_its_own_name(configured, settings):
    """E003 names the binary COMPRESS actually runs."""
    settings.OFFSITE_BACKUP['COMPRESS'] = ['no-such-compressor']
    assert any(
        'no-such-compressor' in message.msg for message in check_settings() if message.id == 'offsite_backup.E003'
    )


def test_directory_store_creates_every_directory_private(tmp_path):
    """Intermediate directories are 0700 too, not only the last one."""
    store = DirectoryStore({'ROOT': str(tmp_path / 'store')})
    source = tmp_path / 'x'
    source.write_text('data')
    store.put(source, 'acme/prod/db/2026-01-01T03-00Z.sql')
    for directory in ('store', 'store/acme', 'store/acme/prod'):
        assert oct((tmp_path / directory).stat().st_mode & 0o777) == '0o700'


def test_a_fake_client_stands_in_for_the_driver(configured, monkeypatch):
    """Under use_fake_s3 a project's tests need no boto3."""
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *a: None if name == 'boto3' else real(name, *a))
    assert 'offsite_backup.E002' not in ids(check_settings())


def test_default_reason_comes_from_the_setting(configured, fake_s3, settings):
    """DEFAULT_REASON replaces 'manual' when --reason is not given."""
    from offsite_backup.notify import capture

    settings.OFFSITE_BACKUP['DEFAULT_REASON'] = 'deploy'
    with capture() as said:
        command('backup_offsite', '--without-media')
    assert said[0][0].reason == 'deploy'


def test_without_media_and_no_reason_prints_a_notice(configured, fake_s3):
    """A deploy that forgot --reason is told so."""
    err = StringIO()
    call_command('backup_offsite', '--without-media', stdout=StringIO(), stderr=err)
    assert '--reason deploy' in err.getvalue()


def test_an_explicit_reason_prints_no_notice(configured, fake_s3):
    """--reason given explicitly is silent, whatever it is."""
    err = StringIO()
    call_command('backup_offsite', '--without-media', '--reason', 'manual', stdout=StringIO(), stderr=err)
    assert err.getvalue() == ''


def test_restore_takes_the_identity_as_text(configured, fake_s3, now, monkeypatch):
    """identity_text is written to a private file that is gone afterwards."""
    from offsite_backup import restore
    from offsite_backup.pass_ import run

    run(now=now)
    monkeypatch.delenv('AGE_IDENTITY', raising=False)
    seen = []
    real = restore._unseal

    def unseal(parts, key, secret, into):
        seen.append((Path(secret), Path(secret).read_text(), oct(Path(secret).stat().st_mode & 0o777)))
        return real(parts, key, secret, into)

    monkeypatch.setattr(restore, '_unseal', unseal)
    restore_database('latest', 'default', identity_text='AGE-SECRET-KEY-1WAKE')
    path, text, mode = seen[0]
    assert text == 'AGE-SECRET-KEY-1WAKE\n'
    assert mode == '0o600'
    assert not path.exists()


def test_an_identity_file_and_text_together_are_refused(configured):
    """One key, one way."""
    from offsite_backup.exceptions import MisconfiguredError
    from offsite_backup.restore import identity_file

    with pytest.raises(MisconfiguredError, match='not both'), identity_file(__file__, 'AGE-SECRET-KEY-1'):
        pass
