from datetime import datetime, timezone

import pytest

from offsite_backup.testing import FakeS3, media_tree, use_fake_s3
from tests import backends

BUCKET = 'backups'


@pytest.fixture(autouse=True)
def _reset_doubles():
    backends.DUMPED.clear()
    backends.RESTORED.clear()
    backends.HELD.clear()
    backends.CLEARED.clear()
    backends.Recorder.sent.clear()
    backends.Network.called.clear()


@pytest.fixture
def fake_s3():
    fake = FakeS3(page_size=2, multipart_threshold=1024)
    fake.bucket(BUCKET)
    with use_fake_s3(fake):
        yield fake


@pytest.fixture
def media_root(tmp_path):
    return media_tree(tmp_path / 'media', {'a.jpg': b'a' * 300, 'sub/b.png': b'b' * 300, 'thumbs/t.jpg': b't' * 10})


@pytest.fixture
def deploy_dir(tmp_path):
    root = tmp_path / 'deploy'
    root.mkdir()
    (root / 'compose.yml').write_text('services: {}\n')
    (root / ('.' + 'env')).write_text('LIVE=secret\n')
    (tmp_path / 'vars.txt').write_text('# names\nAPP_NAME\nAPP_UNSET\n')
    return root


def build_settings(tmp_path, media_root, deploy_dir, **overrides):
    value = {
        'PREFIX': 'acme/prod',
        'ENV': 'prod',
        'STORE': {
            'BACKEND': 'offsite_backup.stores.s3.S3Store',
            'BUCKET': BUCKET,
            'ACCESS_KEY': 'k',
            'SECRET_KEY': 's',
            'LOCK_DAYS': 13,
        },
        'ENCRYPTION': {'BACKEND': 'offsite_backup.testing.Passthrough', 'RECIPIENTS': ['age1test']},
        'COMPRESS': [],
        'DATABASES': {'BACKEND': 'tests.backends.FakeDatabase', 'ALIASES': ['default']},
        'MEDIA': [{'BACKEND': 'offsite_backup.media.filesystem.DirectorySource', 'ROOT': str(media_root)}],
        'CONFIG': {
            'BACKEND': 'offsite_backup.config.paths.PathsSource',
            'PATHS': [str(deploy_dir)],
            'RENDER_ENV': str(tmp_path / 'vars.txt'),
        },
        'NOTIFY': [{'BACKEND': 'tests.backends.Recorder'}],
        'GUARD': {'CACHE': 'default'},
    }
    value.update(overrides)
    return value


@pytest.fixture
def configured(settings, tmp_path, media_root, deploy_dir, fake_s3):
    settings.OFFSITE_BACKUP = build_settings(tmp_path, media_root, deploy_dir)
    return settings


@pytest.fixture
def now():
    return datetime(2026, 3, 10, 3, 0, tzinfo=timezone.utc)
