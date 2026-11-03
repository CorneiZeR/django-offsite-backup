import logging
from datetime import timedelta

import pytest
from django.core.cache import cache

from offsite_backup import conf, naming
from offsite_backup.check import check_last_night
from offsite_backup.exceptions import BackupMissingError, BackupUncheckedError
from offsite_backup.guard import night_key, run_nightly
from offsite_backup.notifiers.telegram import TelegramNotifier
from offsite_backup.notify import capture, notify, silence, silenced
from offsite_backup.outcome import Outcome
from tests import backends
from tests.conftest import BUCKET

# -- notify -------------------------------------------------------------------------------


def test_the_plugin_silences_this_whole_run():
    """pytest11 entry point: a test run cannot reach a real chat."""
    assert silenced()


def test_silenced_runs_skip_network_notifiers(settings):
    """A notifier that reaches the network is never called under silence."""
    settings.OFFSITE_BACKUP = {
        'NOTIFY': [{'BACKEND': 'tests.backends.Network'}, {'BACKEND': 'tests.backends.Recorder'}]
    }
    assert notify(Outcome(kind='failed', prefix='p', error='x')) == 1
    assert backends.Network.called == []
    assert len(backends.Recorder.sent) == 1


def test_capture_sees_messages_even_when_nothing_is_configured(settings):
    """A test about notification captures what would have been said."""
    settings.OFFSITE_BACKUP = {}
    with capture() as said:
        notify(Outcome(kind='failed', prefix='p', error='x'))
    assert said[0][0].kind == 'failed'


def test_a_notifier_that_does_not_build_is_skipped(settings, caplog):
    """A bad NOTIFY block costs neither the backup nor the other notifiers."""
    settings.OFFSITE_BACKUP = {'NOTIFY': [{'BACKEND': 'no.such.Thing'}, {'BACKEND': 'tests.backends.Recorder'}]}
    assert notify(Outcome(kind='failed', prefix='p', error='x')) == 1


def test_a_custom_formatter_is_used(settings):
    """MESSAGES names a callable; projects may replace the format."""
    settings.OFFSITE_BACKUP = {
        'MESSAGES': 'tests.test_notify_guard_check.shout',
        'NOTIFY': [{'BACKEND': 'tests.backends.Recorder'}],
    }
    notify(Outcome(kind='failed', prefix='p'))
    assert backends.Recorder.sent == [('failed', 'FAILED p')]


def shout(outcome):
    return f'{outcome.kind.upper()} {outcome.prefix}'


class Response:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.content = b'x'
        self.is_success = 200 <= status < 300

    def json(self):
        return self._payload


def telegram(monkeypatch, *responses, **options):
    notifier = TelegramNotifier({'TOKEN': '123:SECRET', 'CHAT_ID': -100, **options})
    sent = []
    queue = list(responses)

    def post(payload):
        sent.append(payload)
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(notifier, '_post', post)
    monkeypatch.setattr('time.sleep', lambda seconds: None)
    return notifier, sent


def test_telegram_posts_html_to_the_topic(monkeypatch):
    """HTML parse mode, the topic as message_thread_id."""
    notifier, sent = telegram(monkeypatch, Response(200, {'ok': True}), TOPIC_ID='7')
    assert notifier.send(Outcome(kind='written', prefix='p'), '<b>hi</b>')
    assert sent[0]['parse_mode'] == 'HTML'
    assert sent[0]['message_thread_id'] == 7


def test_telegram_retries_once_on_429(monkeypatch):
    """One retry, honouring retry_after."""
    notifier, sent = telegram(monkeypatch, Response(429, {'parameters': {'retry_after': 2}}), Response(200, {}))
    assert notifier.send(Outcome(kind='written', prefix='p'), 'x')
    assert len(sent) == 2


def test_telegram_refusal_is_false_and_logged_without_the_token(monkeypatch, caplog):
    """A wrong topic sends nothing; the log names the refusal, never the token."""
    notifier, _ = telegram(monkeypatch, Response(400, {'description': 'message thread not found'}), TOPIC_ID=9)
    with caplog.at_level(logging.ERROR, logger='offsite_backup'):
        assert not notifier.send(Outcome(kind='written', prefix='p'), 'x')
    assert 'SECRET' not in caplog.text
    assert any(getattr(record, 'ob_description', '') == 'message thread not found' for record in caplog.records)


def test_telegram_unreachable_never_raises(monkeypatch, caplog):
    """An outage is a False and a log line with the error class only."""
    notifier, _ = telegram(monkeypatch, ConnectionError('https://api.telegram.org/bot123:SECRET/sendMessage'))
    with caplog.at_level(logging.ERROR, logger='offsite_backup'):
        assert not notifier.send(Outcome(kind='written', prefix='p'), 'x')
    assert 'SECRET' not in caplog.text
    assert 'SECRET' not in str([record.__dict__ for record in caplog.records])


# -- guard --------------------------------------------------------------------------------


@pytest.fixture
def clear_cache():
    cache.clear()
    yield
    cache.clear()


def test_the_second_nightly_run_of_a_night_is_skipped(configured, fake_s3, now, clear_cache):
    """A scheduler firing twice writes one backup."""
    assert run_nightly(now=now) is not None
    assert run_nightly(now=now + timedelta(minutes=5)) is None
    assert backends.DUMPED == ['default']


def test_a_night_being_written_is_skipped(configured, fake_s3, now, clear_cache):
    """A claim held by another worker is respected."""
    cache.add(f'{night_key(conf.read(), now)}:running', 1)
    assert run_nightly(now=now) is None
    assert backends.DUMPED == []


def test_a_failed_pass_gives_the_night_back(configured, fake_s3, now, clear_cache, settings):
    """The retry runs."""
    settings.OFFSITE_BACKUP['DATABASES']['FAIL'] = ['default']
    with pytest.raises(Exception, match='not backed up'):
        run_nightly(now=now)
    settings.OFFSITE_BACKUP['DATABASES']['FAIL'] = []
    assert run_nightly(now=now) is not None


def test_done_is_written_before_running_is_released(configured, fake_s3, now, clear_cache, monkeypatch):
    """No moment where neither key holds the night."""
    order = []
    original_set, original_delete = cache.set, cache.delete
    monkeypatch.setattr(cache, 'set', lambda key, *a, **k: order.append(('set', key)) or original_set(key, *a, **k))
    monkeypatch.setattr(
        cache, 'delete', lambda key, *a, **k: order.append(('delete', key)) or original_delete(key, *a, **k)
    )
    run_nightly(now=now)
    assert [action for action, _ in order] == ['set', 'delete']
    assert order[0][1].endswith(':done')


def test_the_next_night_runs(configured, fake_s3, now, clear_cache):
    """Done is per UTC night."""
    run_nightly(now=now)
    assert run_nightly(now=now + timedelta(days=1)) is not None


def test_guard_off_runs_every_time(configured, fake_s3, now, clear_cache, settings):
    """GUARD ENABLED False is an unguarded nightly pass."""
    settings.OFFSITE_BACKUP['GUARD'] = {'ENABLED': False}
    run_nightly(now=now)
    run_nightly(now=now)
    assert backends.DUMPED == ['default', 'default']


def test_nightly_on_an_unconfigured_machine_does_nothing(settings):
    """Off is not a failure for the scheduler."""
    settings.OFFSITE_BACKUP = {}
    assert run_nightly() is None


# -- morning check ------------------------------------------------------------------------


def put(fake, key):
    fake.put_object(Bucket=BUCKET, Key=key, Body=b'x', ObjectLockMode='COMPLIANCE', ObjectLockRetainUntilDate=None)


def test_a_fresh_whole_backup_passes(configured, fake_s3, now):
    """Config and database written within STALE_HOURS."""
    stamp = naming.stamp(now - timedelta(hours=6))
    put(fake_s3, f'acme/prod/config/{stamp}.tar.zst.age')
    put(fake_s3, f'acme/prod/db/{stamp}.sql.zst.age')
    assert check_last_night(now=now) is not None


def test_nothing_at_all_is_missing(configured, fake_s3, now):
    """No backup ever: MISSING, said and raised."""
    with capture() as said, pytest.raises(BackupMissingError):
        check_last_night(now=now)
    assert said[0][0].kind == 'missing'


def test_a_deploy_at_noon_does_not_hide_a_night_that_never_ran(configured, fake_s3, now):
    """Objects of a pass without media do not count."""
    old = naming.stamp(now - timedelta(hours=40))
    fresh = naming.stamp(now - timedelta(hours=2))
    put(fake_s3, f'acme/prod/config/{old}.tar.zst.age')
    put(fake_s3, f'acme/prod/db/{old}.sql.zst.age')
    put(fake_s3, f'acme/prod/config/{fresh}.without-media.tar.zst.age')
    put(fake_s3, f'acme/prod/db/{fresh}.without-media.sql.zst.age')
    with pytest.raises(BackupMissingError):
        check_last_night(now=now)


def test_media_does_not_stand_in_for_a_database(configured, fake_s3, now):
    """Config present, database stale: still missing."""
    fresh = naming.stamp(now - timedelta(hours=2))
    put(fake_s3, f'acme/prod/config/{fresh}.tar.zst.age')
    put(fake_s3, f'acme/prod/media/{fresh}.001.tar.age')
    with pytest.raises(BackupMissingError, match='db/'):
        check_last_night(now=now)


def test_an_unreadable_store_is_unchecked(configured, fake_s3, now):
    """The check that could not look is as loud as missing."""
    del fake_s3.buckets[BUCKET]
    with capture() as said, pytest.raises(BackupUncheckedError):
        check_last_night(now=now)
    assert said[0][0].kind == 'unchecked'


def test_the_check_is_quiet_when_off(settings):
    """A machine without backups has nothing to check."""
    settings.OFFSITE_BACKUP = {}
    assert check_last_night() is None


def test_silence_nests():
    """silence() inside the plugin's silence leaves it on afterwards."""
    with silence():
        assert silenced()
    assert silenced()


# -- database guard -----------------------------------------------------------------------


@pytest.fixture
def database_guard(configured, settings, monkeypatch):
    import contextlib

    from offsite_backup import guard

    held = {'taken': True, 'calls': 0}

    @contextlib.contextmanager
    def lock(alias, key):
        held['calls'] += 1
        yield held['taken']

    monkeypatch.setattr(guard, '_advisory_lock', lock)
    settings.OFFSITE_BACKUP['GUARD'] = {'BACKEND': 'database'}
    return held


def test_database_guard_skips_a_night_another_worker_holds(database_guard, fake_s3, now):
    """The advisory lock taken elsewhere: skipped, nothing dumped."""
    database_guard['taken'] = False
    assert run_nightly(now=now) is None
    assert backends.DUMPED == []


def test_database_guard_reads_done_from_the_store(database_guard, fake_s3, now):
    """No cache: a whole backup stamped tonight is what 'done' means."""
    assert run_nightly(now=now) is not None
    assert run_nightly(now=now + timedelta(hours=1)) is None
    assert run_nightly(now=now + timedelta(days=1)) is not None
    assert backends.DUMPED == ['default', 'default']


def test_database_guard_does_not_count_a_deploy_pass(database_guard, fake_s3, now):
    """A pass without media tonight does not make the night done."""
    from offsite_backup.pass_ import run

    run(media=False, reason='deploy', now=now)
    assert run_nightly(now=now + timedelta(hours=1)) is not None


def test_lock_ids_are_stable_and_per_prefix():
    """Two prefixes never share a lock; one prefix always gets the same one."""
    from offsite_backup.guard import lock_id

    assert lock_id('acme/prod') == lock_id('acme/prod')
    assert lock_id('acme/prod') != lock_id('acme/stage')
    assert -(2**63) <= lock_id('acme/prod') < 2**63


@pytest.mark.filterwarnings('ignore:Overriding setting DATABASES')
def test_guard_backend_checks(configured, settings):
    """An unknown backend and a non-PostgreSQL database are refused; the database backend needs no shared cache."""
    from offsite_backup.checks import check_settings

    settings.OFFSITE_BACKUP['GUARD'] = {'BACKEND': 'redis'}
    assert 'offsite_backup.E001' in [m.id for m in check_settings()]
    settings.OFFSITE_BACKUP['GUARD'] = {'BACKEND': 'database'}
    settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
    found = [m.id for m in check_settings()]
    assert 'offsite_backup.E001' in found
    settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'x'}}
    found = [m.id for m in check_settings()]
    assert 'offsite_backup.E001' not in found
    assert 'offsite_backup.W003' not in found


def test_a_database_that_appeared_after_the_pass_is_not_missing(configured, fake_s3, now, settings):
    """A new tenant served since 03:00 has no backup yet at 09:00; that is not MISSING."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default', 'tenant9']
    stamp = naming.stamp(now - timedelta(hours=6))
    put(fake_s3, f'acme/prod/config/{stamp}.tar.zst.age')
    put(fake_s3, f'acme/prod/db/{stamp}.sql.zst.age')
    assert check_last_night(now=now) is not None


def test_a_database_with_only_old_backups_is_missing(configured, fake_s3, now, settings):
    """A database that was backed up and stopped is MISSING, named."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default', 'tenant9']
    fresh = naming.stamp(now - timedelta(hours=6))
    old = naming.stamp(now - timedelta(hours=60))
    put(fake_s3, f'acme/prod/config/{fresh}.tar.zst.age')
    put(fake_s3, f'acme/prod/db/{fresh}.sql.zst.age')
    put(fake_s3, f'acme/prod/db-tenant9/{old}.sql.zst.age')
    with pytest.raises(BackupMissingError, match='db-tenant9'):
        check_last_night(now=now)


def test_the_only_database_with_no_backup_is_still_missing(configured, fake_s3, now):
    """With nothing else to compare against, an empty database folder is MISSING."""
    put(fake_s3, f'acme/prod/config/{naming.stamp(now - timedelta(hours=6))}.tar.zst.age')
    with pytest.raises(BackupMissingError, match='db/'):
        check_last_night(now=now)


# -- django-aiogram notifier ----------------------------------------------------------------


def topic(**options):
    from offsite_backup.notifiers.django_aiogram import TopicNotifier

    backends.FakeBot.calls.clear()
    return TopicNotifier({'CHAT_ID': -100, 'BOT_FACTORY': 'tests.backends.fake_bot', **options}, label='NOTIFY[0]')


def test_topic_notifier_sends_directly_by_default():
    """direct: send_raw from the process running the pass."""
    assert topic(TOPIC_ID=5).send(Outcome(kind='written', prefix='p'), '<b>x</b>')
    method, function, kwargs = backends.FakeBot.calls[0]
    assert (method, function) == ('send_raw', 'send_message')
    assert kwargs['message_thread_id'] == 5
    assert kwargs['parse_mode'] == 'HTML'


def test_topic_notifier_can_queue():
    """queue: bot.send, which a bot container consumes."""
    assert topic(DELIVERY='queue').send(Outcome(kind='written', prefix='p'), 'x')
    assert backends.FakeBot.calls[0][0] == 'send'


def test_a_zero_topic_is_the_main_thread():
    """int(os.environ.get('X') or 0) means no topic."""
    topic(TOPIC_ID=0).send(Outcome(kind='written', prefix='p'), 'x')
    assert 'message_thread_id' not in backends.FakeBot.calls[0][2]


def test_topic_notifier_checks():
    """Unknown delivery, a factory that does not import or is not callable, a topic that is not an integer."""
    ids = lambda notifier: [message.id for message in notifier.check()]  # noqa: E731
    assert ids(topic()) == []
    assert ids(topic(DELIVERY='later')) == ['offsite_backup.E001']
    assert ids(topic(BOT_FACTORY='tests.backends.no_such')) == ['offsite_backup.E002']
    assert ids(topic(BOT_FACTORY='tests.backends.NOT_CALLABLE')) == ['offsite_backup.E002']
    assert ids(topic(TOPIC_ID='backups')) == ['offsite_backup.E001']


def test_a_factory_that_raises_does_not_cost_the_backup():
    """A broken bot is a False and a log line."""
    assert not topic(BOT_FACTORY='tests.backends.NOT_CALLABLE').send(Outcome(kind='written', prefix='p'), 'x')


def test_database_guard_retries_a_night_where_one_database_failed(database_guard, fake_s3, now, settings):
    """The failed pass uploaded the config and the other database; its retry is not a done night."""
    settings.OFFSITE_BACKUP['DATABASES']['ALIASES'] = ['default', 'tenant1']
    settings.OFFSITE_BACKUP['DATABASES']['FAIL'] = ['tenant1']
    with pytest.raises(Exception, match='not backed up'):
        run_nightly(now=now)
    settings.OFFSITE_BACKUP['DATABASES']['FAIL'] = []
    assert run_nightly(now=now + timedelta(minutes=5)) is not None
    assert run_nightly(now=now + timedelta(minutes=10)) is None
