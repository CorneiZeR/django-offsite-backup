import asyncio

import pytest
from django.core.cache import cache

from tests import backends


@pytest.fixture
def flaky(configured, settings, monkeypatch):
    """The first nightly pass fails (S3 blinked), the next succeeds."""
    from offsite_backup import guard

    real = guard.run
    attempts = []

    def run(**kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise ConnectionError('object storage blinked')
        return real(**kwargs)

    monkeypatch.setattr(guard, 'run', run)
    cache.clear()
    yield attempts
    cache.clear()


def test_celery_retries_the_nightly_pass_through_the_guard(flaky, fake_s3):
    """A failing first attempt is retried; the night is written exactly once."""
    pytest.importorskip('celery')
    from celery import Celery

    app = Celery('t', set_as_current=True)
    app.conf.task_always_eager = True
    app.conf.task_eager_propagates = False
    from offsite_backup.schedule import celery as adapter

    task = app.tasks['offsite_backup.nightly']
    assert task.max_retries == adapter.NIGHTLY_RETRIES
    assert task.autoretry_for == (Exception,)
    result = task.apply()
    assert result.successful(), result.traceback
    assert len(flaky) == 2
    assert backends.DUMPED == ['default']
    assert task.apply().result == {'skipped': True}


def test_celery_does_not_retry_the_morning_check():
    """A MISSING said once is enough; a retry would say it again."""
    pytest.importorskip('celery')
    from celery import Celery

    app = Celery('t', set_as_current=True)
    from offsite_backup.schedule import celery as adapter  # noqa: F401

    assert not getattr(app.tasks['offsite_backup.check'], 'autoretry_for', ())


def test_taskiq_retries_the_nightly_pass_through_the_guard(flaky, fake_s3):
    """The nightly task carries retry labels SimpleRetryMiddleware acts on; the night is written once."""
    pytest.importorskip('taskiq')
    from taskiq import InMemoryBroker, SimpleRetryMiddleware

    from offsite_backup.schedule.taskiq import register

    broker = InMemoryBroker().with_middlewares(SimpleRetryMiddleware())
    nightly, check = register(broker)
    assert nightly.labels['retry_on_error'] is True
    assert nightly.labels['max_retries'] == 3
    assert 'retry_on_error' not in check.labels

    async def go():
        await broker.startup()
        await nightly.kiq()
        await broker.wait_all()
        await broker.shutdown()

    asyncio.run(go())
    assert len(flaky) == 2
    assert backends.DUMPED == ['default']


def test_taskiq_retries_can_be_turned_off():
    """retries=0 registers no retry labels."""
    pytest.importorskip('taskiq')
    from taskiq import InMemoryBroker

    from offsite_backup.schedule.taskiq import register

    nightly, _ = register(InMemoryBroker(), retries=0)
    assert 'retry_on_error' not in nightly.labels
