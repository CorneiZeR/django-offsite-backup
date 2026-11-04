from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from offsite_backup import naming
from offsite_backup.checks import check_deploy
from offsite_backup.drill import run_drill
from offsite_backup.exceptions import DrillFailedError, MisconfiguredError
from offsite_backup.messages.default import format_outcome
from offsite_backup.notify import capture
from offsite_backup.outcome import DRILL_FAILED, DRILLED, Drill, DrilledArchive, DrilledDatabase, Outcome
from offsite_backup.pass_ import run
from tests import backends
from tests.conftest import BUCKET

LIVE = {'default': {'public.a': 3, 'public.b': 2}, 'analytics': {'public.events': 10}}
CONNECTIONS = {
    'default': {'HOST': 'db', 'NAME': 'app'},
    'analytics': {'HOST': 'db', 'NAME': 'analytics'},
    'drill': {'HOST': 'scratch', 'NAME': 'app'},
}


@pytest.fixture
def drilled(configured, settings, monkeypatch, now):
    """Two databases backed up, a throwaway target named, the identity in the environment."""
    settings.OFFSITE_BACKUP['DATABASES'] = {
        'BACKEND': 'tests.backends.FakeDatabase',
        'ALIASES': ['default', 'analytics'],
        'LIVE': LIVE,
        'CONNECTIONS': CONNECTIONS,
    }
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': 'drill'}
    monkeypatch.setenv('AGE_IDENTITY', 'AGE-SECRET-KEY-TEST')
    run(now=now)
    return settings


def test_the_drill_restores_every_database_into_the_target_and_counts_it(drilled, now):
    """Each database is loaded into the throwaway one, counted there and live, and said as passed."""
    with capture() as said:
        report = run_drill(now=now)
    assert [restored[1] for restored in backends.RESTORED] == ['drill', 'drill']
    assert [(found.alias, found.tables, found.rows, found.live_rows) for found in report.databases] == [
        ('analytics', 1, 10, 10),
        ('default', 2, 5, 5),
    ]
    assert said[-1][0].kind == DRILLED
    assert '🗃 default: 2 tables, 5 rows (live: 2 tables, 5 rows)' in said[-1][1]


def test_the_target_is_emptied_after_the_drill(drilled, now):
    """No copy of production stays in the throwaway database."""
    run_drill(now=now)
    assert backends.CLEARED[-1] == 'drill'
    assert backends.HELD['drill'] == {}


def test_a_target_that_reaches_a_live_database_is_refused(drilled, settings, now):
    """Another alias for the same host and database name is the live database, refused by name."""
    settings.OFFSITE_BACKUP['DATABASES']['CONNECTIONS'] = {**CONNECTIONS, 'drill': {'HOST': 'db', 'NAME': 'app'}}
    with capture() as said, pytest.raises(MisconfiguredError, match="which is the live database 'default'"):
        run_drill(now=now)
    assert backends.RESTORED == []
    assert said[-1][0].kind == DRILL_FAILED


def test_a_live_alias_as_the_target_is_refused(drilled, now):
    """Naming a live alias itself is refused before anything is restored."""
    with pytest.raises(MisconfiguredError, match='the live database'):
        run_drill(database='default', now=now)
    assert backends.RESTORED == []


def test_loopback_names_are_one_host():
    """localhost and 127.0.0.1 on the same port and name: the same database."""
    from offsite_backup.databases.postgres import PostgresSource

    connections = {'default': {'HOST': 'localhost', 'NAME': 'app'}, 'drill': {'HOST': '127.0.0.1', 'NAME': 'app'}}
    assert PostgresSource({'CONNECTIONS': connections}).same_database('drill', 'default')


def test_an_empty_port_is_the_default_port():
    """No PORT and PORT 5432 on the same host and name: the same database."""
    from offsite_backup.databases.postgres import PostgresSource

    connections = {'default': {'HOST': 'db', 'NAME': 'app'}, 'drill': {'HOST': 'db', 'PORT': '5432', 'NAME': 'app'}}
    assert PostgresSource({'CONNECTIONS': connections}).same_database('drill', 'default')


def test_a_live_target_is_refused_before_the_store_is_read(configured, settings, monkeypatch, now):
    """Even with nothing stored, a drill aimed at production stops on the target, not on the store."""
    settings.OFFSITE_BACKUP['DATABASES'] = {'BACKEND': 'tests.backends.FakeDatabase', 'CONNECTIONS': CONNECTIONS}
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': 'default'}
    monkeypatch.setenv('AGE_IDENTITY', 'AGE-SECRET-KEY-TEST')
    with pytest.raises(MisconfiguredError, match='the live database'):
        run_drill(now=now)


def test_the_log_notifier_says_a_passed_drill_at_info(caplog):
    """A drill that passed is not an error in the log."""
    import logging

    from offsite_backup.notifiers.log import LogNotifier

    with caplog.at_level(logging.INFO, logger='offsite_backup'):
        LogNotifier({}).send(drill_outcome(), 'text')
        LogNotifier({}).send(drill_outcome(kind=DRILL_FAILED), 'text')
    assert [record.levelname for record in caplog.records] == ['INFO', 'ERROR']


def test_without_a_target_the_drill_refuses(drilled, settings, now):
    """DRILL['DATABASE'] unset: nothing is restored anywhere."""
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': ''}
    with pytest.raises(MisconfiguredError, match=r"DRILL'\]\['DATABASE'\] is not set"):
        run_drill(now=now)
    assert backends.RESTORED == []


def test_one_database_that_does_not_load_does_not_stop_the_others(drilled, settings, now):
    """The failure names the object and the step; the rest is restored; the target is emptied."""
    settings.OFFSITE_BACKUP['DATABASES']['FAIL_RESTORE'] = ['analytics']
    with capture() as said, pytest.raises(DrillFailedError) as raised:
        run_drill(now=now)
    assert "acme/prod/db-analytics/2026-03-10T03-00Z.sql.zst.age: restore into 'drill': BackupError" in str(
        raised.value
    )
    assert [restored[1] for restored in backends.RESTORED] == ['drill', 'drill']
    assert backends.CLEARED[-1] == 'drill'
    assert said[-1][0].kind == DRILL_FAILED
    assert '🗃 default: 2 tables' in said[-1][1]


def test_a_restore_with_no_tables_fails(drilled, settings, now):
    """A dump that loads nothing is not a backup."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default']
    settings.OFFSITE_BACKUP['DATABASES']['LIVE'] = {'default': {}}
    run(now=now.replace(hour=4))
    with pytest.raises(DrillFailedError, match='the restored database has no tables'):
        run_drill(now=now)


def test_a_table_the_restore_lacks_is_named_without_failing(drilled, settings, now):
    """Live grew a table since the pass: said, not failed."""
    settings.OFFSITE_BACKUP['DATABASES']['LIVE'] = {**LIVE, 'default': {**LIVE['default'], 'public.new': 1}}
    with capture() as said:
        report = run_drill(now=now)
    [default] = [found for found in report.databases if found.alias == 'default']
    assert default.missing == ['public.new']
    assert '⚠️ Not in the backup, live now: public.new' in said[-1][1]


def test_a_live_database_that_cannot_be_counted_is_left_uncompared(drilled, settings, now):
    """A read-only role or a tenant gone since: the drill passes without the live figures."""
    settings.OFFSITE_BACKUP['DATABASES']['UNCOUNTABLE'] = ['analytics']
    report = run_drill(now=now)
    [analytics] = [found for found in report.databases if found.alias == 'analytics']
    assert analytics.live_rows is None


def test_compare_live_off_counts_only_the_restore(drilled, settings, now):
    """COMPARE_LIVE False never asks the live database."""
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': 'drill', 'COMPARE_LIVE': False}
    report = run_drill(now=now)
    assert [found.live_rows for found in report.databases] == [None, None]


def test_media_and_config_archives_are_opened(drilled, now):
    """Every volume and the configuration archive are decrypted and listed."""
    report = run_drill(now=now)
    assert {(found.kind, found.files) for found in report.archives} == {('media', 3), ('config', 2)}


def test_without_media_leaves_the_volumes_unopened(drilled, now):
    """The slow part is optional."""
    report = run_drill(media=False, now=now)
    assert [found.kind for found in report.archives] == ['config']


def test_a_volume_that_does_not_open_fails_by_its_key(drilled, fake_s3, now):
    """A cut-short or corrupt volume is named, and the rest still comes back."""
    stamp = naming.stamp(now)
    key = f'acme/prod/media/{stamp}.001.tar.age'
    fake_s3.put_object(Bucket=BUCKET, Key=key, Body=b'not a tar at all' * 10)
    with pytest.raises(DrillFailedError, match=f'{key}: list: ReadError'):
        run_drill(now=now)


def test_the_identity_comes_from_the_drill_setting(drilled, settings, monkeypatch, tmp_path, now):
    """DRILL['IDENTITY_FILE'] stands in for AGE_IDENTITY."""
    monkeypatch.delenv('AGE_IDENTITY')
    key = tmp_path / 'drill.key'
    key.write_text('AGE-SECRET-KEY-TEST\n')
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': 'drill', 'IDENTITY_FILE': str(key)}
    assert run_drill(now=now).stamp == naming.stamp(now)


def test_no_identity_fails_loudly(drilled, monkeypatch, now):
    """Without a key nothing is restored, and the failure is said."""
    monkeypatch.delenv('AGE_IDENTITY')
    with capture() as said, pytest.raises(MisconfiguredError, match='no identity'):
        run_drill(now=now)
    assert said[-1][0].kind == DRILL_FAILED


def test_e011_names_a_drill_target_that_is_live(drilled, settings):
    """check --deploy reports the drill that would refuse, before the month it runs."""
    assert 'offsite_backup.E011' not in [message.id for message in check_deploy()]
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': 'default'}
    assert 'offsite_backup.E011' in [message.id for message in check_deploy()]


def test_e011_names_a_drill_target_that_names_nothing(drilled, settings):
    """A target alias that resolves to no database is the same finding."""
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': 'nope'}
    assert 'offsite_backup.E011' in [message.id for message in check_deploy()]


def test_backup_drill_says_what_came_back(drilled):
    """The command prints the stamp, the databases and the archives."""
    out = StringIO()
    call_command('backup_drill', stdout=out)
    assert "2 database(s) into 'drill', 2 archive(s) opened" in out.getvalue()


def test_backup_drill_fails_non_zero(drilled, settings):
    """A failed drill is a failed command, for the scheduler's failure mail."""
    settings.OFFSITE_BACKUP['DRILL'] = {'DATABASE': ''}
    with pytest.raises(CommandError, match='backup_drill: '):
        call_command('backup_drill')


# -- messages -------------------------------------------------------------------------------


def drill_outcome(kind=DRILLED, **fields):
    report = Drill(
        stamp='2026-03-10T03-00Z',
        prefix='acme/prod',
        target='drill',
        databases=[DrilledDatabase('default', 'k', tables=46, rows=69535, live_tables=46, live_rows=69600)],
        archives=[
            DrilledArchive('media', 'm1', files=10, bytes=1024**2),
            DrilledArchive('media', 'm2', files=5, bytes=1024**2),
            DrilledArchive('config', 'c', files=4, bytes=1024),
        ],
        seconds=812,
    )
    return Outcome(kind=kind, prefix='acme/prod', env='prod', reason='drill', drill=report, **fields)


def test_a_passed_drill_says_what_came_back():
    """What, where, which backup; a line per database and per kind of archive; hashtags last."""
    assert format_outcome(drill_outcome()).splitlines() == [
        '🧪 <b>Restore drill passed</b>: <code>acme/prod</code>',
        '📅 Backup of 2026-03-10T03-00Z, restored into <code>drill</code>',
        '',
        '🗃 default: 46 tables, 69 535 rows (live: 46 tables, 69 600 rows)',
        '🖼 media: 2 volumes, 15 files, 2.0 MB',
        '⚙️ config: 4 files, 0.0 MB',
        '⏱ Took: 812s',
        '',
        '#backup #prod #drill #ok',
    ]


def test_a_failed_drill_says_the_error_escaped():
    """The error is escaped like every other value; tagged #failed."""
    text = format_outcome(drill_outcome(kind=DRILL_FAILED, error='db: <boom>'))
    assert text.splitlines()[0] == '🚨 <b>Restore drill FAILED</b>: <code>acme/prod</code>'
    assert '<pre>db: &lt;boom&gt;</pre>' in text
    assert text.endswith('#backup #prod #drill #failed')


def test_a_failed_upgrade_pass_says_the_upgrade_stops():
    """Before an upgrade, the reader learns the running service keeps serving."""
    text = format_outcome(Outcome(kind='failed', prefix='p', name='the registry', reason='upgrade', error='x'))
    assert 'The upgrade stops here; the running the registry keeps serving.' in text
