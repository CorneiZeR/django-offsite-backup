import hashlib
import hmac
import json
import logging

import pytest

from offsite_backup.checks import check_settings
from offsite_backup.notifiers.webhook import SIGNATURE_HEADER, WebhookNotifier, signature
from offsite_backup.outcome import Outcome, Summary
from offsite_backup.registry import SHIPPED
from offsite_backup.stores.base import Wrote

URL = 'https://hooks.example.com/backup/TOKEN123'


class Response:
    def __init__(self, status):
        self.status_code = status
        self.is_success = 200 <= status < 300


def webhook(monkeypatch, *answers, **options):
    notifier = WebhookNotifier({'URL': URL, **options}, label="OFFSITE_BACKUP['NOTIFY'][0]")
    sent = []
    queue = list(answers)

    def post(body, headers):
        sent.append((body, headers))
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(notifier, '_post', post)
    monkeypatch.setattr('time.sleep', lambda seconds: None)
    return notifier, sent


def written():
    summary = Summary(stamp='2026-03-10T03-00Z', prefix='acme/prod', reason='nightly', media=True)
    summary.uploaded.append(Wrote('acme/prod/db/2026-03-10T03-00Z.sql.zst.age', 2048))
    return Outcome(kind='written', prefix='acme/prod', env='prod', reason='nightly', summary=summary)


def test_the_body_is_the_outcome_as_data_and_the_text(monkeypatch):
    """A receiver routes on kind and shows the text; times are ISO strings, figures numbers."""
    notifier, sent = webhook(monkeypatch, Response(200))
    assert notifier.send(written(), '💾 written')
    body = json.loads(sent[0][0])
    assert body['kind'] == 'written'
    assert body['text'] == '💾 written'
    assert body['summary']['bytes'] == 2048
    assert body['summary']['keys'] == ['acme/prod/db/2026-03-10T03-00Z.sql.zst.age']
    assert sent[0][1]['Content-Type'] == 'application/json'


def test_a_secret_signs_the_exact_body(monkeypatch):
    """The header is HMAC-SHA256 of the bytes sent, which the receiver can recompute."""
    notifier, sent = webhook(monkeypatch, Response(204), SECRET='s3cret')
    notifier.send(written(), 'x')
    body, headers = sent[0]
    expected = 'sha256=' + hmac.new(b's3cret', body, hashlib.sha256).hexdigest()
    assert headers[SIGNATURE_HEADER] == expected == signature('s3cret', body)


def test_without_a_secret_nothing_is_signed(monkeypatch):
    """No secret, no signature header."""
    notifier, sent = webhook(monkeypatch, Response(200))
    notifier.send(written(), 'x')
    assert SIGNATURE_HEADER not in sent[0][1]


def test_extra_headers_are_sent(monkeypatch):
    """HEADERS travel with every request."""
    notifier, sent = webhook(monkeypatch, Response(200), HEADERS={'Authorization': 'Bearer abc'})
    notifier.send(written(), 'x')
    assert sent[0][1]['Authorization'] == 'Bearer abc'


def test_a_busy_receiver_is_retried(monkeypatch):
    """A 503 then a 200: delivered on the second attempt."""
    notifier, sent = webhook(monkeypatch, Response(503), Response(200))
    assert notifier.send(written(), 'x')
    assert len(sent) == 2


@pytest.mark.parametrize('status', [429, 500, 501, 507, 599])
def test_every_5xx_and_a_429_is_retried(monkeypatch, status):
    """The receiver busy or broken, whichever 5xx it answers: another attempt."""
    notifier, sent = webhook(monkeypatch, Response(status), Response(200))
    assert notifier.send(written(), 'x')
    assert len(sent) == 2


def test_a_network_error_is_retried_then_given_up(monkeypatch, caplog):
    """RETRIES more attempts, then False and one log line with the class only — never the URL."""
    error = ConnectionError(URL)
    notifier, sent = webhook(monkeypatch, error, error, RETRIES=1)
    with caplog.at_level(logging.ERROR, logger='offsite_backup'):
        assert not notifier.send(written(), 'x')
    assert len(sent) == 2
    assert 'TOKEN123' not in str([record.__dict__ for record in caplog.records])
    assert any(getattr(record, 'ob_error', '') == 'ConnectionError' for record in caplog.records)


def test_a_refusal_is_final(monkeypatch, caplog):
    """A 400 is the receiver's answer: no retry, False, the status logged."""
    notifier, sent = webhook(monkeypatch, Response(400), Response(200))
    with caplog.at_level(logging.ERROR, logger='offsite_backup'):
        assert not notifier.send(written(), 'x')
    assert len(sent) == 1
    assert any(getattr(record, 'ob_status', 0) == 400 for record in caplog.records)


def test_an_outage_never_raises(monkeypatch):
    """Whatever the client raises, send answers False."""
    notifier, _ = webhook(monkeypatch, RuntimeError('boom'), RuntimeError('boom'), RuntimeError('boom'))
    assert notifier.send(written(), 'x') is False


@pytest.mark.parametrize(
    ('options', 'named'),
    [
        ({'URL': 'ftp://example.com/x'}, "['URL']"),
        ({'URL': URL, 'TIMEOUT': 0}, "['TIMEOUT']"),
        ({'URL': URL, 'TIMEOUT': 'soon'}, "['TIMEOUT']"),
        ({'URL': URL, 'RETRIES': -1}, "['RETRIES']"),
        ({'URL': URL, 'RETRIES': 1.5}, "['RETRIES']"),
        ({'URL': URL, 'HEADERS': ['Authorization']}, "['HEADERS']"),
    ],
)
def test_an_option_of_the_wrong_form_is_e010(options, named):
    """Each option is checked on its own, and named."""
    [error] = [
        message for message in WebhookNotifier(options, label='W').check() if message.id == 'offsite_backup.E010'
    ]
    assert named in error.msg


def test_good_options_have_no_finding():
    """A plain https URL with the defaults passes."""
    assert WebhookNotifier({'URL': URL, 'SECRET': 'x', 'HEADERS': {'A': 'b'}}).check() == []


def test_an_empty_url_is_e001(configured, settings):
    """URL is required: blank is named like any required option."""
    settings.OFFSITE_BACKUP['NOTIFY'] = [{'BACKEND': 'offsite_backup.notifiers.webhook.WebhookNotifier', 'URL': ''}]
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E001']
    assert "OFFSITE_BACKUP['NOTIFY'][0]['URL']" in error.msg


def test_the_request_is_a_json_post_with_the_signature(monkeypatch):
    """Through httpx2 itself: POST to URL, the body as sent, the signature header."""
    httpx2 = pytest.importorskip('httpx2')
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200)

    real = httpx2.Client
    monkeypatch.setattr(httpx2, 'Client', lambda **kwargs: real(transport=httpx2.MockTransport(handler), **kwargs))
    notifier = WebhookNotifier({'URL': URL, 'SECRET': 'k', 'TIMEOUT': 3})
    assert notifier.send(written(), 'x')
    [request] = seen
    assert request.method == 'POST'
    assert str(request.url) == URL
    assert request.headers[SIGNATURE_HEADER] == signature('k', request.content)
    assert json.loads(request.content)['kind'] == 'written'


def test_the_webhook_extra_is_named_when_httpx2_is_missing():
    """The registry knows the driver and the extra without importing the notifier."""
    assert SHIPPED['offsite_backup.notifiers.webhook.WebhookNotifier'] == ('httpx2', 'webhook')
