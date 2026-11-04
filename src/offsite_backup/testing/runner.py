"""Django's test runner, silenced — ``--parallel`` workers included. Imported only by a Django project."""

import os
from typing import Any

from django.test.runner import DiscoverRunner, ParallelTestSuite

from offsite_backup.notify import SILENCE_ENV, silence, silence_process

__all__ = ('OffsiteBackupRunnerMixin', 'OffsiteBackupTestRunner', 'SilencedParallelTestSuite')


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
