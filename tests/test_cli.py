"""The command line: a TOML setting, no Django project. Runs where Django is not installed, too."""

import json
import tarfile
from pathlib import Path

import pytest

from offsite_backup import conf
from offsite_backup.cli import expand, load_config, main, read_env_file
from tests import backends

TOML = """
PREFIX = "acme/registry"
ENV = "prod"
NAME = "the registry"
COMPRESS = []

[STORE]
BACKEND = "offsite_backup.stores.filesystem.DirectoryStore"
ROOT = "${BACKUP_ROOT}"

[ENCRYPTION]
BACKEND = "offsite_backup.testing.Passthrough"
RECIPIENTS = "${BACKUP_AGE_RECIPIENT:-age1test}"

[CONFIG]
BACKEND = "offsite_backup.config.paths.PathsSource"
PATHS = ["${DEPLOY_DIR}"]

[[NOTIFY]]
BACKEND = "tests.backends.Recorder"
"""


@pytest.fixture
def server(tmp_path, monkeypatch):
    """A plain server: a directory store, a deployment directory, the setting beside it."""
    (tmp_path / 'deploy').mkdir()
    (tmp_path / 'deploy' / 'compose.yml').write_text('services: {}\n')
    (tmp_path / 'backups').mkdir()
    setting = tmp_path / 'offsite-backup.toml'
    setting.write_text(TOML)
    monkeypatch.setenv('BACKUP_ROOT', str(tmp_path / 'backups'))
    monkeypatch.setenv('DEPLOY_DIR', str(tmp_path / 'deploy'))
    monkeypatch.chdir(tmp_path / 'backups')  # relative paths must follow the file, not the shell
    return setting


def stored(tmp_path):
    return sorted(path.relative_to(tmp_path / 'backups').as_posix() for path in (tmp_path / 'backups').rglob('*.age'))


def test_run_writes_a_pass_from_the_toml_setting(server, tmp_path, capsys):
    """The same pass as under Django: the config archive under <PREFIX>/config/, the message said."""
    assert main(['--config', str(server), 'run', '--reason', 'nightly']) == 0
    [key] = stored(tmp_path)
    assert key.startswith('acme/registry/config/')
    assert 'offsite-backup: 1 objects' in capsys.readouterr().out
    assert backends.Recorder.sent[-1][0] == 'written'


def test_a_relative_render_env_is_read_against_the_setting_file(server, tmp_path):
    """RENDER_ENV, which reads BASE_DIR under Django, reads the setting file's directory here."""
    (tmp_path / 'vars.txt').write_text('APP_NAME\n')
    server.write_text(TOML.replace('[[NOTIFY]]', 'RENDER_ENV = "vars.txt"\n\n[[NOTIFY]]'))
    main(['--config', str(server), 'run'])
    [key] = stored(tmp_path)
    with tarfile.open(tmp_path / 'backups' / key) as tar:
        assert 'rendered.env' in tar.getnames()


def test_run_path_adds_a_file_to_the_archive(server, tmp_path):
    """--path is what a backup script decides at run time: the newest archive it just wrote."""
    archive = tmp_path / 'app_backup.tar'
    archive.write_bytes(b'x' * 10)
    main(['--config', str(server), 'run', '--path', str(archive)])
    [key] = stored(tmp_path)
    with tarfile.open(tmp_path / 'backups' / key) as tar:
        assert {'deploy/compose.yml', 'app_backup.tar'} <= set(tar.getnames())


def test_run_path_without_a_config_block_makes_one(server, tmp_path):
    """No [CONFIG] at all: --path alone is the archive."""
    server.write_text(TOML.split('[CONFIG]', maxsplit=1)[0] + '[[NOTIFY]]\nBACKEND = "tests.backends.Recorder"\n')
    archive = tmp_path / 'dump.custom'
    archive.write_bytes(b'PGDMP')
    assert main(['--config', str(server), 'run', '--path', str(archive)]) == 0
    [key] = stored(tmp_path)
    with tarfile.open(tmp_path / 'backups' / key) as tar:
        assert tar.getnames() == ['dump.custom']


def test_the_setting_comes_from_the_environment_variable(server, monkeypatch):
    """OFFSITE_BACKUP_CONFIG stands in for --config."""
    monkeypatch.setenv('OFFSITE_BACKUP_CONFIG', str(server))
    assert main(['run']) == 0


def test_no_setting_is_a_usage_error(monkeypatch, capsys):
    """Neither --config nor the variable: 2, and how to give one."""
    monkeypatch.delenv('OFFSITE_BACKUP_CONFIG', raising=False)
    assert main(['run']) == 2
    assert 'OFFSITE_BACKUP_CONFIG' in capsys.readouterr().err


def test_a_missing_file_is_a_usage_error(tmp_path, capsys):
    """A path that is not there is named."""
    assert main(['--config', str(tmp_path / 'nope.toml'), 'run']) == 2
    assert 'nope.toml' in capsys.readouterr().err


def test_off_is_nothing_to_do(tmp_path, capsys):
    """No store credentials: run exits 0 and says so; checks answers 3."""
    setting = tmp_path / 'off.toml'
    setting.write_text('PREFIX = "acme/prod"\n')
    assert main(['--config', str(setting), 'run']) == 0
    assert 'nothing to do' in capsys.readouterr().out
    assert main(['--config', str(setting), 'checks']) == 3


def test_partly_configured_fails_by_name(server, monkeypatch, capsys):
    """A store root with no recipient source: 1, naming the setting."""
    server.write_text(
        TOML.replace('offsite_backup.testing.Passthrough', 'offsite_backup.encryption.age_cli.AgeCli').replace(
            '${BACKUP_AGE_RECIPIENT:-age1test}', '${BACKUP_AGE_RECIPIENT}'
        )
    )
    assert main(['--config', str(server), 'run']) == 1
    assert "OFFSITE_BACKUP['ENCRYPTION']['RECIPIENTS']" in capsys.readouterr().err


def test_checks_print_findings_and_fail_on_an_error(server, capsys):
    """W001 and W002 are printed and pass; an error makes it 1."""
    assert main(['--config', str(server), 'checks']) == 0
    err = capsys.readouterr().err
    assert 'offsite_backup.W001' in err
    assert 'offsite_backup.W002' in err
    server.write_text(TOML.replace('PREFIX = "acme/registry"', 'PREFIX = ""'))
    assert main(['--config', str(server), 'checks']) == 1
    assert 'offsite_backup.E001' in capsys.readouterr().err


def test_checks_deploy_runs_the_path_checks(server, tmp_path, capsys):
    """--deploy adds what a pass would find missing here."""
    server.write_text(TOML.replace('PATHS = ["${DEPLOY_DIR}"]', 'PATHS = ["${DEPLOY_DIR}", "/not-mounted"]'))
    main(['--config', str(server), 'checks'])
    assert 'offsite_backup.W005' not in capsys.readouterr().err
    main(['--config', str(server), 'checks', '--deploy'])
    assert 'offsite_backup.W005' in capsys.readouterr().err


def test_failed_says_a_failure_met_before_the_pass(server):
    """A dump that died in the caller's script is reported in the same format."""
    assert main(['--config', str(server), 'failed', '--reason', 'upgrade', 'the dump died']) == 0
    kind, text = backends.Recorder.sent[-1]
    assert kind == 'failed'
    assert 'the dump died' in text
    assert 'The upgrade stops here; the running the registry keeps serving.' in text


def test_check_and_status_after_a_pass(server, capsys):
    """The morning check passes; status prints the config folder's age."""
    main(['--config', str(server), 'run'])
    assert main(['--config', str(server), 'check']) == 0
    assert main(['--config', str(server), 'status', '--json']) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index('{') :])['kinds'][0]['kind'] == 'config'


def test_status_is_one_when_nothing_was_ever_written(server):
    """No backup at all is stale."""
    assert main(['--config', str(server), 'status']) == 1


def test_list_and_restore(server, tmp_path, monkeypatch, capsys):
    """What the store holds, and the newest whole backup back on disk."""
    main(['--config', str(server), 'run'])
    assert main(['--config', str(server), 'list', '--json']) == 0
    out = capsys.readouterr().out
    assert json.loads(out[out.index('{') :])['kinds'][0]['kind'] == 'config'
    monkeypatch.setenv('AGE_IDENTITY', 'AGE-SECRET-KEY-TEST')
    into = tmp_path / 'restored'
    assert main(['--config', str(server), 'restore', '--stamp', 'latest', '--into', str(into)]) == 0
    assert list((into / 'config').iterdir())


def test_a_failing_pass_exits_one_and_names_it(server, capsys):
    """A required path missing: 1, and the path named."""
    server.write_text(TOML.replace('PATHS = ["${DEPLOY_DIR}"]', 'PATHS = ["/gone"]\nREQUIRED = true'))
    assert main(['--config', str(server), 'run']) == 1
    assert 'required path(s) missing' in capsys.readouterr().err


def test_the_setting_is_dropped_after_the_command(server):
    """A Django process that calls main() goes back to its own settings."""
    main(['--config', str(server), 'run'])
    assert not conf.standalone()


# -- the setting -----------------------------------------------------------------------------


def test_expand_reads_variables_with_defaults_and_a_literal_dollar():
    """${NAME}, ${NAME:-default}, $$; an unset name is blank."""
    environment = {'SET': 'v', 'BLANK': ''}
    assert expand('${SET}', environment) == 'v'
    assert expand('${UNSET}', environment) == ''
    assert expand('${UNSET:-d}', environment) == 'd'
    assert expand('${BLANK:-d}', environment) == 'd'
    assert expand('a$$b', environment) == 'a$b'
    assert expand({'k': ['${SET}', 3]}, environment) == {'k': ['v', 3]}


def test_env_files_win_over_the_environment_and_later_files_over_earlier(tmp_path, monkeypatch):
    """Two root-only files, the key file last: its values are the ones used."""
    setting = tmp_path / 's.toml'
    setting.write_text('A = "${A}"\nB = "${B}"\n')
    first = tmp_path / 'config.env'
    first.write_text('# settings\nA=from-config\nB=from-config\n')
    second = tmp_path / 'key.env'
    second.write_text("export B='from-key'\n")
    monkeypatch.setenv('A', 'from-environment')
    assert load_config(setting, [first, second]) == {'A': 'from-config', 'B': 'from-key'}


def test_read_env_file_skips_comments_and_strips_one_pair_of_quotes(tmp_path):
    """The KEY=value shape a role renders."""
    path = tmp_path / 'x.env'
    path.write_text('# c\n\nA="1"\nB=two=2\nnot a pair\n')
    assert read_env_file(path) == {'A': '1', 'B': 'two=2'}


def test_a_missing_env_file_is_named(tmp_path, server, capsys):
    """--env-file that is not there is a usage error by name."""
    assert main(['--config', str(server), '--env-file', str(tmp_path / 'key.env'), 'run']) == 2
    assert 'key.env' in capsys.readouterr().err


def test_a_broken_toml_file_is_named(tmp_path, capsys):
    """A syntax error says which file."""
    setting = tmp_path / 'bad.toml'
    setting.write_text('PREFIX = \n')
    assert main(['--config', str(setting), 'run']) == 2
    assert 'bad.toml' in capsys.readouterr().err


def test_the_secret_never_needs_argv(tmp_path, monkeypatch):
    """Everything the store needs comes from the file and the environment: argv holds only paths."""
    setting = Path(tmp_path / 's.toml')
    setting.write_text('[STORE]\nSECRET_KEY = "${SECRET}"\n')
    monkeypatch.setenv('SECRET', 'hunter2')
    assert load_config(setting)['STORE']['SECRET_KEY'] == 'hunter2'
