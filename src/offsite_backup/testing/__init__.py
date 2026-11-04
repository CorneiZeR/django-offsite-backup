"""What a project's tests import: a fake S3, a sealer that needs no binary, and silence.

Notifiers are silenced for every pytest run by the plugin this package registers, and for
Django's runner by ``OffsiteBackupTestRunner``: a CI job that inherits a production chat
token must still never post to that chat. ``capture()`` sees every message regardless.

The runner classes need Django and are imported from ``testing.runner`` when first named,
so the fakes and ``Passthrough`` serve a suite with no Django project too.
"""

import contextlib
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from offsite_backup import findings as checks
from offsite_backup.encryption.base import Sealer
from offsite_backup.notify import capture, silence, silenced
from offsite_backup.testing.fake_s3 import FakeBucket, FakeClientError, FakeS3

if TYPE_CHECKING:
    from offsite_backup.testing.runner import (
        OffsiteBackupRunnerMixin,
        OffsiteBackupTestRunner,
        SilencedParallelTestSuite,
    )

__all__ = (
    'FakeBucket',
    'FakeClientError',
    'FakeS3',
    'OffsiteBackupRunnerMixin',
    'OffsiteBackupTestRunner',
    'Passthrough',
    'SilencedParallelTestSuite',
    'capture',
    'media_tree',
    'silence',
    'silenced',
    'use_fake_s3',
)


_RUNNER = frozenset({'OffsiteBackupRunnerMixin', 'OffsiteBackupTestRunner', 'SilencedParallelTestSuite'})


def __getattr__(name: str) -> Any:
    """The runner classes, imported with Django when first named."""
    if name not in _RUNNER:
        msg = f'module {__name__!r} has no attribute {name!r}'
        raise AttributeError(msg)
    from offsite_backup.testing import runner  # noqa: PLC0415 - needs Django

    return getattr(runner, name)


class Passthrough(Sealer):
    """``cat`` instead of encryption — for tests on machines without ``age``. Never in production."""

    BINARIES = ('cat',)

    def pipeline(self, extra_recipients: Sequence[str] = ()) -> list[str]:
        """``cat``."""
        return ['cat']

    def unseal(self, identity: str) -> list[str]:
        """``cat``."""
        return ['cat']

    def check(self) -> list[checks.CheckMessage]:
        """W002, always: nothing sealed by this is encrypted."""
        return [
            *super().check(),
            checks.Warning(
                f'{self.label} is the test Passthrough: backups are NOT encrypted.',
                hint='Use offsite_backup.encryption.age_cli.AgeCli outside tests.',
                id='offsite_backup.W002',
            ),
        ]


@contextlib.contextmanager
def use_fake_s3(fake: FakeS3 | None = None) -> Iterator[FakeS3]:
    """Every S3 client the package builds inside the block is ``fake``."""
    from offsite_backup.stores import s3  # noqa: PLC0415 - the module whose factory is replaced

    fake = fake or FakeS3()
    previous = s3.CLIENT_FACTORY
    s3.CLIENT_FACTORY = lambda _options: fake
    try:
        yield fake
    finally:
        s3.CLIENT_FACTORY = previous


def media_tree(root: Path, files: dict[str, bytes]) -> Path:
    """Write ``{relative path: bytes}`` under ``root``; return ``root``."""
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root
