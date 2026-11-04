"""Against a real Docker daemon: a named volume read without mounting it, and nothing started.

Run with ``OFFSITE_BACKUP_TEST_DOCKER=1`` (the integration leg sets it); skipped otherwise,
so the unit legs do not pull an image.
"""

import os
import secrets
import shutil
import subprocess
import tarfile

import pytest

pytestmark = pytest.mark.integration

if not os.environ.get('OFFSITE_BACKUP_TEST_DOCKER'):
    pytest.skip('OFFSITE_BACKUP_TEST_DOCKER is not set', allow_module_level=True)
if shutil.which('docker') is None or subprocess.run(['docker', 'info'], capture_output=True, check=False).returncode:
    pytest.skip('no Docker daemon to ask', allow_module_level=True)

IMAGE = 'busybox:stable'


def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def volume():
    name = f'ob-media-{secrets.token_hex(4)}'
    docker('volume', 'create', name)
    docker('pull', '-q', IMAGE)
    script = (
        'mkdir -p /v/photos/2026 && head -c 300000 /dev/urandom > /v/photos/2026/one.jpg '
        '&& printf hello > "/v/with space.txt" && ln -s /etc/passwd /v/link'
    )
    docker('run', '--rm', f'--volume={name}:/v', IMAGE, 'sh', '-c', script)
    yield name
    docker('volume', 'rm', '--force', name)


def test_a_named_volume_is_backed_up_byte_for_byte_and_nothing_runs(volume, tmp_path):
    """The bytes in the volumes equal the bytes in the Docker volume; no container is left behind."""
    from offsite_backup.media.docker_volume import VolumeSource
    from offsite_backup.sealing import Sealing
    from offsite_backup.testing import Passthrough

    before = set(docker('ps', '--all', '--quiet', '--filter', 'label=offsite_backup=media').split())
    found = {}

    def ship(path, rows):
        with tarfile.open(path) as tar:
            found.update({member.name: tar.extractfile(member).read() for member in tar})
        found['rows'] = rows

    VolumeSource({'VOLUME': volume, 'IMAGE': IMAGE}, label='MEDIA').seal_volumes(
        lambda number: tmp_path / f'{number}.tar', Sealing(Passthrough({})), ship
    )
    rows = found.pop('rows')
    assert sorted(found) == ['photos/2026/one.jpg', 'with space.txt']
    assert found['with space.txt'] == b'hello'
    copied = docker('run', '--rm', f'--volume={volume}:/v:ro', IMAGE, 'sha256sum', '/v/photos/2026/one.jpg')
    assert copied.split()[0] == next(row.sha256 for row in rows if row.path == 'photos/2026/one.jpg')
    after = set(docker('ps', '--all', '--quiet', '--filter', 'label=offsite_backup=media').split())
    assert after == before


def test_a_volume_that_does_not_exist_is_not_created(tmp_path):
    """Asked first: a typo stays a typo, not an empty volume backed up every night."""
    from offsite_backup.media.docker_volume import VolumeSource

    name = f'ob-absent-{secrets.token_hex(4)}'
    assert list(VolumeSource({'VOLUME': name, 'IMAGE': IMAGE}).items()) == []
    assert name not in docker('volume', 'ls', '--quiet').split()
