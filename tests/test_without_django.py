"""The core without Django: every module but the Django layer imports, and a whole pass runs.

Each case runs in a subprocess where ``import django`` fails, so it proves the same where
Django is installed; CI also runs this file in an environment without Django at all.
"""

import subprocess
import sys
import textwrap

BLOCK_DJANGO = """
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] == 'django':
            raise ImportError(f'blocked {name}')
sys.meta_path.insert(0, Block())
"""

#: the Django layer: the app, the commands, the schedulers' tasks, the test runner
DJANGO_LAYER = (
    'offsite_backup.apps',
    'offsite_backup.management.',
    'offsite_backup.schedule.',
    'offsite_backup.testing.runner',
)


def without_django(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    script = BLOCK_DJANGO + textwrap.dedent(code)
    return subprocess.run([sys.executable, '-c', script, *args], capture_output=True, text=True, check=False)


def test_every_module_outside_the_django_layer_imports_without_django():
    """The core, every backend and the command line need no Django."""
    result = without_django(
        f"""
        import importlib, pkgutil, offsite_backup
        layer = {DJANGO_LAYER!r}
        for module in pkgutil.walk_packages(offsite_backup.__path__, 'offsite_backup.'):
            if module.name.startswith(layer) or module.name in {{'offsite_backup.management'}}:
                continue
            importlib.import_module(module.name)
        print('ok')
        """
    )
    assert result.stdout.strip() == 'ok', result.stderr


def test_a_whole_pass_runs_from_the_command_line_without_django(tmp_path):
    """run, then status and the morning check: the same pass, rotation and messages, no Django."""
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy' / 'compose.yml').write_text('services: {}\n')
    (tmp_path / 'media').mkdir()
    (tmp_path / 'media' / 'a.jpg').write_bytes(b'a' * 100)
    setting = tmp_path / 'offsite-backup.toml'
    setting.write_text(
        textwrap.dedent(
            f"""
            PREFIX = "acme/service"
            COMPRESS = []

            [STORE]
            BACKEND = "offsite_backup.stores.filesystem.DirectoryStore"
            ROOT = "{tmp_path / 'backups'}"

            [ENCRYPTION]
            BACKEND = "offsite_backup.testing.Passthrough"

            [[MEDIA]]
            BACKEND = "offsite_backup.media.filesystem.DirectorySource"
            ROOT = "{tmp_path / 'media'}"

            [CONFIG]
            BACKEND = "offsite_backup.config.paths.PathsSource"
            PATHS = ["{tmp_path / 'deploy'}"]

            [[NOTIFY]]
            BACKEND = "offsite_backup.notifiers.log.LogNotifier"
            """
        )
    )
    result = without_django(
        """
        import sys
        from offsite_backup.cli import main
        codes = [main(['--config', sys.argv[1], *command.split()]) for command in ('run --reason nightly', 'status', 'check')]
        assert 'django' not in sys.modules, 'django was imported'
        print(codes)
        """,
        str(setting),
    )
    assert result.stdout.strip().splitlines()[-1] == '[0, 0, 0]', result.stderr
    # the media volume and its manifest
    assert sorted(path.parent.name for path in (tmp_path / 'backups').rglob('*.age')) == ['config', 'media', 'media']
