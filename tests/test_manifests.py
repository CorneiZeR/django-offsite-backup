import hashlib
import io
import json
import tarfile
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from offsite_backup import naming
from offsite_backup.exceptions import BackupError
from offsite_backup.media.base import MediaEntry, MediaSource, Packer, pack
from offsite_backup.pass_ import run
from offsite_backup.restore import restore_media
from offsite_backup.stores.s3 import S3Store
from tests.conftest import BUCKET

FILES = {'a.jpg': b'a' * 300, 'sub/b.png': b'b' * 300, 'thumbs/t.jpg': b't' * 10}
#: room for one 300-byte file per volume, so every file is a volume of its own
ONE_PER_VOLUME = 20 * 1024 + 300 + 2048


def body(fake, key):
    return fake.buckets[BUCKET].keys[key][-1].data


def media_keys(fake, suffix):
    return sorted(key for key in fake.buckets[BUCKET].keys if '/media/' in key and key.endswith(suffix))


@pytest.fixture
def downloads(monkeypatch):
    got = []
    real = S3Store.get

    def get(self, key, into):
        got.append(key)
        return real(self, key, into)

    monkeypatch.setattr(S3Store, 'get', get)
    return got


@pytest.fixture
def split(configured, settings):
    settings.OFFSITE_BACKUP['MEDIA'][0]['VOLUME_BYTES'] = ONE_PER_VOLUME
    return settings


def test_every_volume_has_a_manifest_of_what_it_holds(configured, fake_s3, now):
    """Path, size and SHA-256 of each file, beside the volume it is in."""
    run(now=now)
    stamp = naming.stamp(now)
    [manifest] = media_keys(fake_s3, '.manifest.json.age')
    assert manifest == f'acme/prod/media/{stamp}.001.manifest.json.age'
    document = json.loads(body(fake_s3, manifest))
    assert document['volume'] == f'{stamp}.001.tar.age'
    assert document['files'] == [
        {'path': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in FILES.items()
    ]


def test_the_manifest_records_the_bytes_written_not_the_size_listed(tmp_path):
    """A file that changed after listing is described as it went into the tar."""
    from offsite_backup.media.filesystem import DirectorySource
    from offsite_backup.sealing import Sealing
    from offsite_backup.testing import Passthrough, media_tree

    root = media_tree(tmp_path / 'm', {'a.jpg': b'1'})
    source = DirectorySource({'ROOT': str(root)})
    shipped = []
    original = source.items

    def items():
        for entry, opener in original():
            (root / 'a.jpg').write_bytes(b'12345')
            yield entry, opener

    source.items = items
    source.seal_volumes(
        lambda number: tmp_path / f'{number}.tar', Sealing(Passthrough({})), lambda *a: shipped.append(a)
    )
    [(_, rows)] = shipped
    assert rows[0].size == 5
    assert rows[0].sha256 == hashlib.sha256(b'12345').hexdigest()


def test_a_volume_is_shipped_before_the_next_one_is_written(configured, fake_s3, now, split, monkeypatch):
    """Streamed: the workspace holds one sealed volume at a time, so media larger than the disk works."""
    seen = []
    real = MediaSource.items

    def items(self):
        for entry, opener in real(self):

            def opened(entry=entry, opener=opener):
                seen.append((entry.name, len(media_keys(fake_s3, '.tar.age'))))
                return opener()

            yield entry, opened

    monkeypatch.setattr(MediaSource, 'items', items)
    run(now=now)
    assert seen == [('a.jpg', 0), ('sub/b.png', 1), ('thumbs/t.jpg', 2)]


def test_packing_while_streaming_cuts_where_pack_cuts():
    """One budget rule for the listed packing and the streamed one."""
    entries = [MediaEntry(f'f{index}', size) for index, size in enumerate([100, 100, 10**6, 100, 100, 100])]
    limit = 20 * 1024 + 2 * (100 + 2048)
    packer = Packer(limit)
    cuts = [index for index, entry in enumerate(entries) if packer.place(entry)]
    assert cuts == [0, 2, 3, 5]
    assert [len(volume) for volume in pack(entries, limit)] == [2, 1, 2, 1]


def test_one_file_is_restored_from_its_volume_alone(configured, fake_s3, now, split, tmp_path, downloads):
    """The manifests say where the file is; no other volume is downloaded."""
    run(now=now)
    [restored] = restore_media('latest', ['sub/b.png'], into=tmp_path / 'out', identity=__file__)
    path = restored.path
    assert restored.verified
    assert path == (tmp_path / 'out' / 'media' / 'sub' / 'b.png').resolve()
    assert path.read_bytes() == FILES['sub/b.png']
    volumes = [key for key in downloads if key.endswith('.tar.age')]
    assert volumes == [f'acme/prod/media/{naming.stamp(now)}.002.tar.age']


def test_a_folder_restores_everything_under_it_and_nothing_beside(configured, fake_s3, now, tmp_path, media_root):
    """A prefix is a folder: ``sub`` is ``sub/...``, never ``subway/...``."""
    (media_root / 'subway.jpg').write_bytes(b's')
    run(now=now)
    restored = restore_media(naming.stamp(now), ['sub/'], into=tmp_path / 'out', identity=__file__)
    assert [item.path.name for item in restored] == ['b.png']


def test_nothing_matching_is_an_error(configured, fake_s3, now, tmp_path):
    """A path that no manifest lists is said, not an empty directory."""
    run(now=now)
    with pytest.raises(BackupError, match='matches nope'):
        restore_media('latest', ['nope'], into=tmp_path / 'out', identity=__file__)


def test_a_file_that_does_not_match_its_manifest_is_refused(configured, fake_s3, now, tmp_path):
    """The SHA-256 the pass recorded is checked on the way out."""
    run(now=now)
    [volume] = media_keys(fake_s3, '.tar.age')
    data = bytearray(body(fake_s3, volume))
    offset = data.index(b'a' * 300)
    data[offset] = ord('X')
    fake_s3.buckets[BUCKET].keys[volume][-1].data = bytes(data)
    with pytest.raises(BackupError, match='does not match the manifest'):
        restore_media('latest', ['a.jpg'], into=tmp_path / 'out', identity=__file__)


def test_a_volume_missing_what_its_manifest_lists_is_refused(configured, fake_s3, now, tmp_path):
    """A manifest that promises a file the volume lacks is not a silent partial restore."""
    run(now=now)
    [volume] = media_keys(fake_s3, '.tar.age')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as tar:
        member = tarfile.TarInfo('sub/b.png')
        member.size = 300
        tar.addfile(member, io.BytesIO(FILES['sub/b.png']))
    fake_s3.buckets[BUCKET].keys[volume][-1].data = buffer.getvalue()
    with pytest.raises(BackupError, match=r'lacks what its manifest lists: a\.jpg'):
        restore_media('latest', ['a.jpg', 'sub'], into=tmp_path / 'out', identity=__file__)


def test_a_volume_without_a_manifest_is_read_whole(configured, fake_s3, now, tmp_path):
    """A pass written before manifests existed still restores one file."""
    run(now=now)
    for key in media_keys(fake_s3, '.manifest.json.age'):
        del fake_s3.buckets[BUCKET].keys[key]
    [restored] = restore_media('latest', ['a.jpg'], into=tmp_path / 'out', identity=__file__)
    assert restored.path.read_bytes() == FILES['a.jpg']
    assert not restored.verified


def test_backup_restore_path_restores_media_files(configured, fake_s3, now, tmp_path):
    """--path on the command line, repeatable."""
    run(now=now)
    out = StringIO()
    call_command(
        'backup_restore',
        '--stamp',
        'latest',
        '--identity',
        __file__,
        '--path',
        'a.jpg',
        '--path',
        'thumbs',
        '--into',
        str(tmp_path),
        stdout=out,
    )
    lines = out.getvalue().splitlines()
    assert sorted(line.split('/')[-1] for line in lines) == ['a.jpg  (verified)', 't.jpg  (verified)']


def test_backup_restore_path_does_not_combine_with_apply(configured):
    """Media files and a database load are different requests."""
    with pytest.raises(CommandError, match='--path'):
        call_command('backup_restore', '--stamp', 'latest', '--path', 'a.jpg', '--apply', '--database', 'default')


def test_backup_restore_database_fetches_that_dump_alone(configured, fake_s3, now, tmp_path, settings):
    """--database without --apply downloads one database's dump and nothing else."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default', 'reports']
    run(now=now)
    out = StringIO()
    call_command(
        'backup_restore',
        '--stamp',
        'latest',
        '--identity',
        __file__,
        '--database',
        'reports',
        '--into',
        str(tmp_path),
        stdout=out,
    )
    [line] = out.getvalue().splitlines()
    assert '/db-reports/' in line
    assert line.endswith('(verified)')


def restore_command(tmp_path, *args):
    out = StringIO()
    call_command(
        'backup_restore', '--stamp', 'latest', '--identity', __file__, '--into', str(tmp_path), *args, stdout=out
    )
    return out.getvalue().splitlines()


def test_backup_restore_database_narrows_kind(configured, fake_s3, now, tmp_path, settings):
    """--database with --kind is the intersection: that database alone, not the kinds added to it."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default', 'reports']
    run(now=now)
    [line] = restore_command(tmp_path, '--kind', 'media', '--kind', 'db-reports', '--database', 'reports')
    assert '/db-reports/' in line


def test_backup_restore_database_outside_kind_is_refused(configured, fake_s3, now, tmp_path, settings):
    """An empty intersection is said, not widened into fetching both."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default', 'reports']
    run(now=now)
    with pytest.raises(CommandError, match='leaves out'):
        restore_command(tmp_path, '--kind', 'media', '--database', 'reports')
