import pytest

from offsite_backup import conf
from offsite_backup.exceptions import MisconfiguredError, NotConfiguredError


def test_absent_setting_is_off(settings):
    """No setting at all: off, and load says so."""
    if hasattr(settings, 'OFFSITE_BACKUP'):
        del settings.OFFSITE_BACKUP
    assert not conf.enabled()
    with pytest.raises(NotConfiguredError):
        conf.load()


def test_empty_credentials_are_off(settings):
    """A development machine that holds no key is off, not partly configured."""
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'acme/prod',
        'STORE': {'BACKEND': 'offsite_backup.stores.s3.S3Store', 'BUCKET': '', 'ACCESS_KEY': '', 'REGION': 'eu'},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': []},
    }
    assert not conf.enabled()


def test_one_credential_set_is_on_and_names_every_missing_one(settings):
    """A machine that lost one variable is refused by name, never read as off."""
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'acme/prod',
        'STORE': {'BACKEND': 'offsite_backup.stores.s3.S3Store', 'BUCKET': 'b', 'ACCESS_KEY': '', 'SECRET_KEY': ''},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': []},
        'DATABASES': {'BACKEND': 'offsite_backup.databases.postgres.PostgresSource'},
    }
    assert conf.enabled()
    with pytest.raises(MisconfiguredError) as error:
        conf.load()
    text = str(error.value)
    assert "OFFSITE_BACKUP['STORE']['ACCESS_KEY']" in text
    assert "OFFSITE_BACKUP['STORE']['SECRET_KEY']" in text
    assert "OFFSITE_BACKUP['ENCRYPTION']['RECIPIENTS']" in text
    assert "['BUCKET']" not in text


def test_explicit_enabled_wins(settings):
    """ENABLED False turns a fully configured machine off."""
    settings.OFFSITE_BACKUP = {'ENABLED': False, 'STORE': {'BACKEND': 'x', 'BUCKET': 'b'}}
    assert not conf.enabled()


def test_nothing_to_back_up_is_a_problem(settings):
    """A store and a key with no source is partly configured."""
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'p',
        'STORE': {'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore', 'ROOT': '/tmp/x'},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': ['age1x']},
    }
    assert any('nothing to back up' in problem for problem in conf.problems())


def test_missing_prefix_is_a_problem(settings):
    """Every key lives under PREFIX; without one, rotation has no scope."""
    settings.OFFSITE_BACKUP = {
        'STORE': {'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore', 'ROOT': '/tmp/x'},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': ['age1x']},
        'CONFIG': {'BACKEND': 'offsite_backup.config.paths.PathsSource'},
    }
    assert "OFFSITE_BACKUP['PREFIX']" in conf.problems()


@pytest.mark.parametrize('keep', [0, -1, '14', None, True])
def test_keep_nights_must_be_a_positive_integer(settings, keep):
    """KEEP_NIGHTS outside the positive integers is refused."""
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'p',
        'STORE': {'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore', 'ROOT': '/tmp/x'},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': ['age1x']},
        'CONFIG': {'BACKEND': 'offsite_backup.config.paths.PathsSource'},
        'RETENTION': {'KEEP_NIGHTS': keep},
    }
    assert any('KEEP_NIGHTS' in problem for problem in conf.problems())


def test_nested_blocks_merge_with_their_defaults(settings):
    """GUARD given partly keeps the rest of its defaults."""
    settings.OFFSITE_BACKUP = {'GUARD': {'CACHE': 'shared'}}
    guard = conf.read()['GUARD']
    assert guard['CACHE'] == 'shared'
    assert guard['RUNNING_SECONDS'] == 3600
