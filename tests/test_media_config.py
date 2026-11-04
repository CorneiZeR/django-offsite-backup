import io
import tarfile

import pytest

from offsite_backup.config.paths import REBUILT_FILE, PathsSource, env_line, rendered_environment
from offsite_backup.media.base import MEMBER_OVERHEAD, VOLUME_OVERHEAD, MediaEntry, pack
from offsite_backup.media.filesystem import DirectorySource
from offsite_backup.media.s3 import S3Source
from offsite_backup.sealing import Sealing
from offsite_backup.testing import FakeS3, Passthrough, media_tree, use_fake_s3

LIMIT = VOLUME_OVERHEAD + 3 * (100 + MEMBER_OVERHEAD)


def entries(*sizes):
    return [MediaEntry(f'f{index}', size) for index, size in enumerate(sizes)]


def test_pack_fills_volumes_in_order_with_whole_files():
    """Files go in order; a volume closes before it would pass the limit."""
    volumes = pack(entries(100, 100, 100, 100), LIMIT)
    assert [[entry.name for entry in volume] for volume in volumes] == [['f0', 'f1', 'f2'], ['f3']]


def test_pack_gives_an_oversized_file_a_volume_of_its_own():
    """A file larger than a volume is not refused: it travels alone."""
    volumes = pack(entries(100, 10**6, 100), LIMIT)
    assert [[entry.name for entry in volume] for volume in volumes] == [['f0'], ['f1'], ['f2']]


def test_pack_of_nothing_is_no_volume():
    """Empty is not an error and writes no empty archive."""
    assert pack([], LIMIT) == []


def test_filters_include_and_exclude(tmp_path):
    """INCLUDE narrows, EXCLUDE removes (e.g. regenerable thumbnails)."""
    root = media_tree(tmp_path, {'a.jpg': b'1', 'thumbs/a.jpg': b'1', 'doc.pdf': b'1'})
    source = DirectorySource({'ROOT': str(root), 'INCLUDE': ['*.jpg'], 'EXCLUDE': ['thumbs/*']})
    assert [entry.name for entry in source.selected()] == ['a.jpg']


def test_directory_source_skips_symlinks(tmp_path):
    """A link out of the media root is not followed."""
    root = media_tree(tmp_path / 'm', {'a.jpg': b'1'})
    (tmp_path / 'secret').write_text('x')
    (root / 'link').symlink_to(tmp_path / 'secret')
    assert [entry.name for entry in DirectorySource({'ROOT': str(root)}).entries()] == ['a.jpg']


def test_a_file_that_grew_after_listing_is_archived_whole(tmp_path):
    """The size is taken when the file is opened, so a changed file does not break the tar."""
    root = media_tree(tmp_path / 'm', {'a.jpg': b'1'})
    source = DirectorySource({'ROOT': str(root)})
    volume = source.volumes()[0]
    (root / 'a.jpg').write_bytes(b'12345')
    out = source.seal_volume(volume, tmp_path / 'v.tar', Sealing(Passthrough({})))
    with tarfile.open(out) as tar:
        assert tar.extractfile('a.jpg').read() == b'12345'


def test_s3_source_reads_every_page():
    """Every object under the prefix, across pages, with its key as the archive path."""
    fake = FakeS3(page_size=2)
    fake.bucket('media', lock_enabled=False)
    for name in ('private/media/a', 'private/media/b', 'private/media/c', 'public/x'):
        fake.put_object(Bucket='media', Key=name, Body=b'data')
    with use_fake_s3(fake):
        source = S3Source({'BUCKET': 'media', 'PREFIX': 'private/media/'})
        names = [entry.name for entry in source.entries()]
        assert names == ['private/media/a', 'private/media/b', 'private/media/c']


def test_s3_source_volume_round_trips(tmp_path):
    """Objects are streamed into the tar with their bytes intact."""
    fake = FakeS3()
    fake.bucket('media', lock_enabled=False)
    fake.put_object(Bucket='media', Key='m/a.jpg', Body=b'image')
    with use_fake_s3(fake):
        source = S3Source({'BUCKET': 'media'})
        out = source.seal_volume(source.volumes()[0], tmp_path / 'v', Sealing(Passthrough({})))
    with tarfile.open(out) as tar:
        assert tar.extractfile('m/a.jpg').read() == b'image'


@pytest.mark.parametrize(
    ('value', 'line'),
    [
        ('plain-value_1.2:3', 'NAME=plain-value_1.2:3'),
        ('has space', "NAME='has space'"),
        ('', "NAME=''"),
        ("it's $HOME", 'NAME="it\'s \\$HOME"'),
        ('a\nb', '# NAME contains a newline, which this format cannot carry'),
    ],
)
def test_env_line_quotes_so_both_readers_agree(value, line):
    """Quoting keeps the literal value for compose and a shell; a newline cannot forge lines."""
    assert env_line('NAME', value) == line


def test_rendered_environment_says_what_was_unset(tmp_path, monkeypatch):
    """An unset name is written down, not skipped."""
    names = tmp_path / 'vars.txt'
    names.write_text('# comment\nAPP_NAME  # trailing\n\nAPP_UNSET\n')
    monkeypatch.setenv('APP_NAME', 'shop')
    monkeypatch.delenv('APP_UNSET', raising=False)
    text = rendered_environment(names)
    assert 'APP_NAME=shop' in text
    assert '# APP_UNSET was not set' in text


def test_the_config_archive_carries_paths_and_the_rebuilt_environment(tmp_path, deploy_dir, monkeypatch):
    """Paths under their own name, the rebuilt environment at the root, the live one never."""
    monkeypatch.setenv('APP_NAME', 'shop')
    source = PathsSource({'PATHS': [str(deploy_dir)], 'RENDER_ENV': str(tmp_path / 'vars.txt')})
    out = source.archive(tmp_path / 'c.tar', Sealing(Passthrough({})))
    with tarfile.open(out) as tar:
        names = sorted(tar.getnames())
        rebuilt = tar.extractfile(REBUILT_FILE).read().decode()
    assert names == ['deploy/compose.yml', REBUILT_FILE]
    assert 'APP_NAME=shop' in rebuilt
    assert 'LIVE=secret' not in rebuilt


def test_a_missing_path_is_w005_under_deploy_checks_only(tmp_path):
    """Where a pass runs (check --deploy) a missing mount is warned; a plain check says nothing."""
    source = PathsSource({'PATHS': [str(tmp_path / 'nope')]}, label='CONFIG')
    assert [message.id for message in source.deploy_check()] == ['offsite_backup.W005']
    assert source.check() == []


def test_a_missing_names_file_is_w005_under_deploy_checks_only(tmp_path):
    """RENDER_ENV not found is the same warning, in the same place."""
    source = PathsSource({'PATHS': [], 'RENDER_ENV': str(tmp_path / 'vars.txt')}, label='CONFIG')
    assert [message.id for message in source.deploy_check()] == ['offsite_backup.W005']
    assert source.check() == []


def test_a_missing_media_root_is_w005_under_deploy_checks_only(tmp_path):
    """A media ROOT not mounted here is warned where a pass runs, not on every manage.py."""
    source = DirectorySource({'ROOT': str(tmp_path / 'nope')}, label='MEDIA[0]')
    assert [message.id for message in source.deploy_check()] == ['offsite_backup.W005']
    assert source.check() == []


def test_a_missing_optional_path_is_logged_by_the_pass(tmp_path, caplog):
    """The worker, which runs no system check, still says which path it left out."""
    gone = tmp_path / 'nope'
    source = PathsSource({'PATHS': [str(gone)]}, label='CONFIG')
    with caplog.at_level('WARNING', logger='offsite_backup'):
        source.archive(tmp_path / 'c.tar', Sealing(Passthrough({})))
    [record] = [r for r in caplog.records if r.getMessage() == 'Configuration path not found; left out']
    assert record.ob_path == str(gone)


def test_a_missing_media_root_is_logged_by_the_pass(tmp_path, caplog):
    """A media pass over a directory that is not here says so instead of an empty night in silence."""
    gone = tmp_path / 'nope'
    with caplog.at_level('WARNING', logger='offsite_backup'):
        assert DirectorySource({'ROOT': str(gone)}).volumes() == []
    [record] = [r for r in caplog.records if r.getMessage() == 'Media directory not found']
    assert record.ob_path == str(gone)


def test_tar_reads_back_from_a_stream(tmp_path):
    """Volumes are plain tar streams any tar reads."""
    root = media_tree(tmp_path / 'm', {'x/y.txt': b'hello'})
    source = DirectorySource({'ROOT': str(root)})
    out = source.seal_volume(source.volumes()[0], tmp_path / 'v', Sealing(Passthrough({})))
    with tarfile.open(fileobj=io.BytesIO(out.read_bytes()), mode='r|') as tar:
        assert [member.name for member in tar] == ['x/y.txt']


def test_a_directory_source_without_any_root_refuses(settings):
    """An empty ROOT and MEDIA_ROOT never archive the working directory."""
    from offsite_backup.exceptions import MisconfiguredError

    settings.MEDIA_ROOT = ''
    source = DirectorySource({}, label='MEDIA')
    with pytest.raises(MisconfiguredError):
        list(source.entries())
    assert [message.id for message in source.check()] == ['offsite_backup.E001']


def test_a_custom_exclude_keeps_the_live_environment_out(tmp_path, deploy_dir):
    """EXCLUDE adds to the default; it never lets the live environment file travel."""
    source = PathsSource({'PATHS': [str(deploy_dir)], 'EXCLUDE': ['*.log']})
    out = source.archive(tmp_path / 'c.tar', Sealing(Passthrough({})))
    with tarfile.open(out) as tar:
        assert tar.getnames() == ['deploy/compose.yml']
