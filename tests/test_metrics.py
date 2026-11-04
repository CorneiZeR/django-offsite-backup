import json
from datetime import datetime, timedelta, timezone
from io import StringIO

import pytest
from django.core.management import call_command

from offsite_backup import metrics
from offsite_backup.exceptions import BackupUncheckedError
from offsite_backup.notify import capture
from offsite_backup.pass_ import run


def ages(found):
    return {age.kind: (age.seconds, age.stale) for age in found.kinds}


def test_every_kind_has_its_age(configured, fake_s3, now):
    """Config, each database and each media source, measured from the newest whole backup."""
    run(now=now)
    found = metrics.freshness(now=now + timedelta(hours=6))
    assert ages(found) == {'config': (6 * 3600, False), 'db': (6 * 3600, False), 'media': (6 * 3600, False)}


def test_older_than_stale_hours_is_stale(configured, fake_s3, now):
    """Past CHECK['STALE_HOURS'] a kind is stale; nothing is said in the chat."""
    run(now=now)
    with capture() as said:
        found = metrics.freshness(now=now + timedelta(hours=27))
    assert [age.kind for age in found.stale] == ['config', 'db', 'media']
    assert said == []


def test_a_kind_with_nothing_is_stale(configured, fake_s3, now):
    """No backup at all is stale, with no age."""
    found = metrics.freshness(now=now)
    assert ages(found)['db'] == (None, True)


def test_a_pass_without_media_does_not_count(configured, fake_s3, now):
    """A deploy's pass is not a whole backup, as for the morning check."""
    run(now=now)
    run(media=False, reason='deploy', now=now + timedelta(hours=20))
    found = metrics.freshness(now=now + timedelta(hours=21))
    assert ages(found)['config'] == (21 * 3600, False)


def test_stale_hours_can_be_given(configured, fake_s3, now):
    """--stale-hours overrides the setting."""
    run(now=now)
    assert metrics.freshness(now=now + timedelta(hours=2), stale_hours=1).stale


def test_an_unreadable_store_raises_unchecked(configured, fake_s3, now, monkeypatch):
    """The store failing is its own answer, not stale."""
    from offsite_backup.stores.s3 import S3Store

    monkeypatch.setattr(S3Store, 'current', lambda *a: (_ for _ in ()).throw(ConnectionError('down')))
    with pytest.raises(BackupUncheckedError, match='ConnectionError: down'):
        metrics.freshness(now=now)


def test_prometheus_text(configured, fake_s3, now):
    """Per kind the last success and staleness; up and the time asked, labelled by prefix."""
    run(now=now)
    text = metrics.prometheus(metrics.freshness(now=now + timedelta(hours=1)))
    stamp = f'{now.timestamp():.0f}'
    assert f'offsite_backup_last_success_timestamp_seconds{{prefix="acme/prod",kind="db"}} {stamp}' in text
    assert 'offsite_backup_stale{prefix="acme/prod",kind="media"} 0' in text
    assert 'offsite_backup_up{prefix="acme/prod"} 1' in text
    assert '# TYPE offsite_backup_stale gauge' in text


def test_prometheus_leaves_out_a_kind_with_no_backup_but_calls_it_stale():
    """No sample for a time that never happened; stale says it."""
    found = metrics.Freshness(
        prefix='p',
        checked=datetime(2026, 1, 1, tzinfo=timezone.utc),
        stale_hours=26,
        kinds=[metrics.Age(kind='db', newest=None, seconds=None, stale=True)],
    )
    text = metrics.prometheus(found)
    assert 'last_success_timestamp_seconds{' not in text
    assert 'offsite_backup_stale{prefix="p",kind="db"} 1' in text


def test_label_values_are_escaped():
    """A quote or backslash in a prefix cannot break the exposition format."""
    found = metrics.Freshness(prefix='a"b\\c', checked=datetime(2026, 1, 1, tzinfo=timezone.utc), stale_hours=1)
    assert 'offsite_backup_up{prefix="a\\"b\\\\c"} 1' in metrics.prometheus(found)


def test_the_textfile_is_replaced_whole(tmp_path):
    """Written beside and renamed over: a collector reads the old file or the new, never half."""
    target = tmp_path / 'offsite_backup.prom'
    target.write_text('old\n')
    metrics.write_textfile(target, 'new\n')
    assert target.read_text() == 'new\n'
    assert [path.name for path in tmp_path.iterdir()] == ['offsite_backup.prom']


def test_a_write_that_fails_leaves_the_old_file_and_nothing_beside_it(tmp_path, monkeypatch):
    """The file is never written in place: the rename failing keeps the old one whole."""
    import pathlib

    target = tmp_path / 'offsite_backup.prom'
    target.write_text('old\n')

    def refuse(self, other):
        raise OSError('disk full')

    monkeypatch.setattr(pathlib.Path, 'replace', refuse)
    with pytest.raises(OSError, match='disk full'):
        metrics.write_textfile(target, 'new\n')
    assert target.read_text() == 'old\n'
    assert [path.name for path in tmp_path.iterdir()] == ['offsite_backup.prom']


def status(*args):
    out = StringIO()
    try:
        call_command('backup_status', *args, stdout=out, stderr=out)
    except SystemExit as exit_:
        return exit_.code, out.getvalue()
    return 0, out.getvalue()


def test_backup_status_exits_zero_when_fresh(configured, fake_s3, now, monkeypatch):
    """Every kind fresh: 0, a line per kind."""
    run()
    code, out = status()
    assert code == 0
    assert '  db/: ' in out


def test_backup_status_exits_one_when_stale(configured, fake_s3, now):
    """One stale kind: 1, and the kind marked."""
    run(now=now - timedelta(days=3))
    code, out = status()
    assert code == metrics.STALE
    assert 'STALE' in out


def test_backup_status_exits_two_when_the_store_is_unreadable(configured, fake_s3, monkeypatch, tmp_path):
    """Unreadable: 2, and the textfile says up 0."""
    from offsite_backup.stores.s3 import S3Store

    monkeypatch.setattr(S3Store, 'current', lambda *a: (_ for _ in ()).throw(ConnectionError('down')))
    target = tmp_path / 'm.prom'
    code, out = status('--prometheus', str(target))
    assert code == metrics.UNREADABLE
    assert 'could not be read' in out
    assert 'offsite_backup_up{prefix="acme/prod"} 0' in target.read_text()


def test_backup_status_json_and_textfile(configured, fake_s3, tmp_path):
    """--json for a script, --prometheus for a collector, in one run."""
    run()
    target = tmp_path / 'm.prom'
    code, out = status('--json', '--prometheus', str(target))
    assert code == 0
    assert {kind['kind'] for kind in json.loads(out)['kinds']} == {'config', 'db', 'media'}
    assert 'offsite_backup_stale{prefix="acme/prod",kind="config"} 0' in target.read_text()


def test_backup_status_says_nothing_to_do_when_off(settings):
    """A development machine exits 0."""
    settings.OFFSITE_BACKUP = {}
    assert status() == (0, 'backup_status: not configured on this machine, nothing to do\n')
