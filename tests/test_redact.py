"""The HTTP client logs each request's URL at INFO; a notifier's token or webhook URL never reaches that log."""

import functools
import logging

import pytest

httpx2 = pytest.importorskip('httpx2')  # the [telegram]/[webhook] extra

from offsite_backup.notifiers.telegram import TelegramNotifier
from offsite_backup.notifiers.webhook import WebhookNotifier
from offsite_backup.outcome import Outcome, Summary
from offsite_backup.stores.base import Wrote

TOKEN = '123456:SECRET-token-value'
HOOK = 'https://hooks.example/abc/SECRET-path'


def _answer(request: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json={'ok': True})


@pytest.fixture
def offline(monkeypatch):
    """A real client with a transport that never leaves the process, so the client's own log line is written."""
    monkeypatch.setattr(httpx2, 'Client', functools.partial(httpx2.Client, transport=httpx2.MockTransport(_answer)))


def _outcome() -> Outcome:
    summary = Summary(stamp='2026-03-10T03-00Z', prefix='acme/prod', reason='nightly', media=True)
    summary.uploaded.append(Wrote('acme/prod/db/2026-03-10T03-00Z.sql.zst.age', 2048))
    return Outcome(kind='written', prefix='acme/prod', env='prod', reason='nightly', summary=summary)


def _client_log(caplog) -> str:
    return '\n'.join(record.getMessage() for record in caplog.records if record.name.startswith('httpx'))


def test_the_telegram_token_is_not_in_the_client_log(offline, caplog):
    notifier = TelegramNotifier({'TOKEN': TOKEN, 'CHAT_ID': '-100'}, label="OFFSITE_BACKUP['NOTIFY'][0]")
    with caplog.at_level(logging.INFO, logger='httpx2'):
        assert notifier.send(_outcome(), 'x')
    logged = _client_log(caplog)
    assert 'HTTP Request' in logged
    assert TOKEN not in logged
    assert '<redacted>' in logged


def test_the_webhook_url_is_not_in_the_client_log(offline, caplog):
    notifier = WebhookNotifier({'URL': HOOK}, label="OFFSITE_BACKUP['NOTIFY'][0]")
    with caplog.at_level(logging.INFO, logger='httpx2'):
        assert notifier.send(_outcome(), 'x')
    logged = _client_log(caplog)
    assert 'HTTP Request' in logged
    assert 'SECRET-path' not in logged
