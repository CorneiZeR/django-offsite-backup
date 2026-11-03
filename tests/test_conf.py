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


def test_a_list_of_blank_recipients_is_unset(settings):
    """[os.environ.get('X', '')] on a machine with store keys but no recipient is E001 by name."""
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'p',
        'STORE': {'BACKEND': 'offsite_backup.stores.s3.S3Store', 'BUCKET': 'b', 'ACCESS_KEY': 'k', 'SECRET_KEY': 's'},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': ['', '  ']},
        'CONFIG': {'BACKEND': 'offsite_backup.config.paths.PathsSource'},
    }
    assert "OFFSITE_BACKUP['ENCRYPTION']['RECIPIENTS']" in conf.problems()


def test_a_zero_chat_id_is_unset(settings):
    """int(os.environ.get('X') or 0) is 0, which names no chat."""
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'p',
        'STORE': {'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore', 'ROOT': '/tmp/x'},
        'ENCRYPTION': {'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli', 'RECIPIENTS': 'age1x'},
        'CONFIG': {'BACKEND': 'offsite_backup.config.paths.PathsSource'},
        'NOTIFY': [{'BACKEND': 'offsite_backup.notifiers.telegram.TelegramNotifier', 'TOKEN': 't', 'CHAT_ID': 0}],
    }
    assert "OFFSITE_BACKUP['NOTIFY'][0]['CHAT_ID']" in conf.problems()


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('age1a age1b', ['age1a', 'age1b']),
        ('age1a, age1b', ['age1a', 'age1b']),
        (['age1a', '', ' age1b '], ['age1a', 'age1b']),
        ('ssh-ed25519 AAAAkey user@host', ['ssh-ed25519 AAAAkey user@host']),
        ('', []),
    ],
)
def test_recipients_accept_a_string_or_a_list(value, expected):
    """A separated string splits; an ssh recipient keeps its spaces; blanks drop."""
    from offsite_backup.encryption.age_cli import AgeCli

    assert AgeCli({'RECIPIENTS': value}).recipients == expected


def test_blank_list_items_drop_everywhere(tmp_path):
    """PATHS, INCLUDE/EXCLUDE and ALIASES lists ignore blank items."""
    from offsite_backup.config.paths import PathsSource
    from offsite_backup.databases.postgres import PostgresSource
    from offsite_backup.media.filesystem import DirectorySource

    assert PathsSource({'PATHS': ['', str(tmp_path)]}).paths == [tmp_path]
    assert PostgresSource({'ALIASES': ['default', ' ']}).targets() == ['default']
    (tmp_path / 'a.jpg').write_text('x')
    assert [entry.name for entry in DirectorySource({'ROOT': str(tmp_path), 'INCLUDE': ['']}).selected()] == ['a.jpg']
