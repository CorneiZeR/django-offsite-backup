"""Silence reaches every worker of a parallel run, whatever the start method."""

import multiprocessing
import os
import subprocess
import sys
import textwrap

import pytest

PROJECT_SETTINGS = textwrap.dedent(
    """
    SECRET_KEY = 'parallel'
    INSTALLED_APPS = ['offsite_backup']
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
    TEST_RUNNER = 'offsite_backup.testing.OffsiteBackupTestRunner'
    """
)

WORKER_TESTS = textwrap.dedent(
    """
    import os
    from unittest import TestCase

    from offsite_backup.notify import silenced


    class First(TestCase):
        def test_silenced(self):
            print('PID', os.getpid(), flush=True)
            assert silenced(), 'a worker can reach a real chat'


    class Second(TestCase):
        def test_silenced(self):
            print('PID', os.getpid(), flush=True)
            assert silenced(), 'a worker can reach a real chat'
    """
)

MANAGE = textwrap.dedent(
    """
    import multiprocessing, sys
    if __name__ == '__main__':
        multiprocessing.set_start_method(sys.argv.pop(1), force=True)
        from django.core.management import execute_from_command_line
        execute_from_command_line(sys.argv)
    """
)


def project(tmp_path):
    (tmp_path / 'project_settings.py').write_text(PROJECT_SETTINGS)
    (tmp_path / 'test_workers.py').write_text(WORKER_TESTS)
    (tmp_path / 'manage.py').write_text(MANAGE)
    env = {key: value for key, value in os.environ.items() if not key.startswith('OFFSITE_BACKUP')}
    env.update(DJANGO_SETTINGS_MODULE='project_settings', PYTHONPATH=str(tmp_path))
    return env


@pytest.mark.parametrize(
    'method', [m for m in ('spawn', 'forkserver', 'fork') if m in multiprocessing.get_all_start_methods()]
)
def test_every_parallel_worker_is_silenced(tmp_path, method):
    """manage.py test --parallel 2: each worker sees silenced(), under every start method."""
    env = project(tmp_path)
    result = subprocess.run(
        [sys.executable, 'manage.py', method, 'test', 'test_workers', '--parallel', '2', '-v', '2'],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'Ran 2 tests' in output


def test_every_xdist_worker_is_silenced(tmp_path):
    """pytest -n 2: the plugin loads in each xdist worker."""
    pytest.importorskip('xdist')
    env = project(tmp_path)
    (tmp_path / 'test_workers.py').write_text(
        'from offsite_backup.notify import silenced\n\n'
        'def test_one():\n    assert silenced()\n\n'
        'def test_two():\n    assert silenced()\n'
    )
    result = subprocess.run(
        [
            sys.executable,
            '-m',
            'pytest',
            '-q',
            '-n',
            '2',
            '-p',
            'no:cacheprovider',
            '--rootdir',
            str(tmp_path),
            'test_workers.py',
        ],
        cwd=tmp_path,
        env={**env, 'PYTEST_ADDOPTS': ''},
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '2 passed' in result.stdout


def fresh(code, **env):
    clean = {key: value for key, value in os.environ.items() if not key.startswith('OFFSITE_BACKUP')}
    return subprocess.run(
        [sys.executable, '-c', code], env={**clean, **env}, capture_output=True, text=True, check=False
    )


def test_the_environment_variable_alone_silences_a_fresh_process():
    """A worker that inherited only the environment is silenced."""
    code = 'from offsite_backup.notify import silenced; print(silenced())'
    assert fresh(code).stdout.strip() == 'False'
    assert fresh(code, OFFSITE_BACKUP_SILENCE='1').stdout.strip() == 'True'


def test_the_worker_hook_alone_silences_a_fresh_process():
    """The parallel suite's process_setup silences a worker without the variable."""
    code = (
        'import django; from django.conf import settings; settings.configure(); django.setup()\n'
        'import os; from offsite_backup.testing import SilencedParallelTestSuite as S\n'
        'S.process_setup(); os.environ.pop("OFFSITE_BACKUP_SILENCE")\n'
        'from offsite_backup.notify import silenced; print(silenced())'
    )
    result = fresh(code)
    assert result.stdout.strip() == 'True', result.stderr
