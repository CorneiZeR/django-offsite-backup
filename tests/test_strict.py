import os

import pytest

from offsite_backup.config.paths import PathsSource
from offsite_backup.exceptions import SourceMissingError
from offsite_backup.media.filesystem import DirectorySource
from offsite_backup.notify import capture
from offsite_backup.pass_ import run
from offsite_backup.sealing import Sealing
from offsite_backup.testing import Passthrough

needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason='root reads every file')


def test_a_missing_required_path_fails_the_pass_naming_it(configured, fake_s3, now, settings, tmp_path):
    """REQUIRED on the config block: a pass without that path is not whole, and FAILED says which."""
    gone = tmp_path / 'certs'
    settings.OFFSITE_BACKUP['CONFIG']['PATHS'].append(str(gone))
    settings.OFFSITE_BACKUP['CONFIG']['REQUIRED'] = [str(gone)]
    with capture() as said, pytest.raises(SourceMissingError, match='certs'):
        run(now=now)
    assert said[0][0].kind == 'failed'
    assert 'certs' in said[0][1]


def test_a_missing_optional_path_is_left_out(configured, fake_s3, now, settings, tmp_path):
    """Without REQUIRED or STRICT the pass goes on without it, as before."""
    settings.OFFSITE_BACKUP['CONFIG']['PATHS'].append(str(tmp_path / 'certs'))
    assert run(now=now).failed == {}


def test_strict_makes_every_path_required(configured, fake_s3, now, settings, tmp_path):
    """STRICT: any missing config path fails the pass."""
    settings.OFFSITE_BACKUP['CONFIG']['PATHS'].append(str(tmp_path / 'certs'))
    settings.OFFSITE_BACKUP['STRICT'] = True
    with pytest.raises(SourceMissingError, match='certs'):
        run(now=now)


@needs_permissions
def test_strict_fails_on_an_unreadable_config_file(tmp_path):
    """STRICT: a file that cannot be read is not silently left out."""
    root = tmp_path / 'deploy'
    root.mkdir()
    locked = root / 'signing.key'
    locked.write_text('k')
    locked.chmod(0)
    source = PathsSource({'PATHS': [str(root)]}, label='CONFIG')
    source.archive(tmp_path / 'a.tar', Sealing(Passthrough({})))
    source.strict = True
    with pytest.raises(SourceMissingError, match=r'signing\.key'):
        source.archive(tmp_path / 'b.tar', Sealing(Passthrough({})))


@needs_permissions
def test_strict_fails_on_an_unreadable_media_file(tmp_path):
    """STRICT: an unreadable media file fails the pass instead of being logged and skipped."""
    (tmp_path / 'a.jpg').write_text('x')
    (tmp_path / 'b.jpg').write_text('x')
    (tmp_path / 'b.jpg').chmod(0)
    source = DirectorySource({'ROOT': str(tmp_path)})
    assert [entry.name for entry in source.entries()] == ['a.jpg']
    source.strict = True
    with pytest.raises(SourceMissingError, match=r'b\.jpg'):
        list(source.entries())


def test_an_empty_required_media_source_fails(tmp_path):
    """REQUIRED on a media source: zero files is a failure, not a quiet night."""
    assert DirectorySource({'ROOT': str(tmp_path)}).volumes() == []
    with pytest.raises(SourceMissingError, match='no media files'):
        DirectorySource({'ROOT': str(tmp_path), 'REQUIRED': True}).volumes()


def test_a_missing_required_path_is_e009_under_deploy_checks(tmp_path):
    """check --deploy says it where the deploy runs the pass; a plain check only warns."""
    source = PathsSource({'PATHS': [str(tmp_path / 'certs')], 'REQUIRED': True}, label='CONFIG')
    assert [message.id for message in source.deploy_check()] == ['offsite_backup.E009']
    assert [message.id for message in source.check()] == []
    optional = PathsSource({'PATHS': [str(tmp_path / 'certs')]}, label='CONFIG')
    assert optional.deploy_check() == []
    assert [message.id for message in optional.check()] == ['offsite_backup.W005']
