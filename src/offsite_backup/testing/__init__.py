"""What a project's tests import: a fake S3, a sealer that needs no binary, and silence.

Notifiers are silenced for every pytest run by the plugin this package registers, and for
Django's runner by ``OffsiteBackupTestRunner``: a CI job that inherits a production chat
token must still never post to that chat. ``capture()`` sees every message regardless.
"""

import contextlib
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from django.core import checks
from django.test.runner import DiscoverRunner, ParallelTestSuite

from offsite_backup.encryption.base import Sealer
from offsite_backup.notify import SILENCE_ENV, capture, silence, silence_process, silenced
from offsite_backup.testing.fake_s3 import FakeBucket, FakeClientError, FakeS3

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


def _silence_worker(*args: Any) -> None:
    """Runs in each spawned or forkserver worker before its tests; parent module state never gets there."""
    os.environ[SILENCE_ENV] = '1'
    silence_process()


class SilencedParallelTestSuite(ParallelTestSuite):
    """Django's parallel suite whose every spawned or forkserver worker is silenced too."""

    # a plain function, as Django's own stub is: it calls ``self.process_setup.__func__``
    process_setup = _silence_worker


class OffsiteBackupRunnerMixin:
    """Silence notifiers for a whole ``manage.py test`` run, ``--parallel`` workers included.

    Mix into a project's own runner: ``class Runner(OffsiteBackupRunnerMixin, MyRunner)``.
    """

    parallel_test_suite: type[ParallelTestSuite] = SilencedParallelTestSuite

    def run_tests(self, *args: Any, **kwargs: Any) -> int:
        """Run the suite silenced, with the variable workers inherit set for its length."""
        previous = os.environ.get(SILENCE_ENV)
        os.environ[SILENCE_ENV] = '1'
        try:
            with silence():
                return super().run_tests(*args, **kwargs)  # type: ignore[misc, no-any-return]
        finally:
            if previous is None:
                os.environ.pop(SILENCE_ENV, None)
            else:
                os.environ[SILENCE_ENV] = previous


class OffsiteBackupTestRunner(OffsiteBackupRunnerMixin, DiscoverRunner):
    """Django's runner with every network notifier silenced for the whole run."""


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
