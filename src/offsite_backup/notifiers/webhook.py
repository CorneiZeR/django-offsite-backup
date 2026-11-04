"""A JSON POST to any URL (``httpx2``, the ``[webhook]`` extra) — for whatever is not a chat.

The body is the outcome as data (``Outcome.as_dict()``) beside the formatted ``text``, so a
receiver can route on ``kind`` and still show the message. With a ``SECRET`` every request
carries ``X-Offsite-Backup-Signature: sha256=<hex>``, the HMAC-SHA256 of the exact body
bytes, so a receiver can refuse a body nobody signed. A network error, a 429 or a 5xx is
retried (``RETRIES`` more times, a growing pause between); any other answer is final.
The URL is never logged: a webhook URL often carries its own token.
"""

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from offsite_backup import findings as checks
from offsite_backup.notifiers import redact
from offsite_backup.notifiers.base import Notifier
from offsite_backup.outcome import Outcome

__all__ = ('SIGNATURE_HEADER', 'WebhookNotifier', 'signature')

logger = logging.getLogger('offsite_backup')

SIGNATURE_HEADER = 'X-Offsite-Backup-Signature'
#: worth another attempt: the receiver is busy (429) or broken (any 5xx), not refusing
_BUSY = 429
_BROKEN = range(500, 600)
#: the first pause between attempts, doubled each time
_BACKOFF = 1.0


def _retried(status: int) -> bool:
    return status == _BUSY or status in _BROKEN


def signature(secret: str, body: bytes) -> str:
    """``sha256=<hex>`` of ``body`` under ``secret`` — what a receiver recomputes and compares."""
    return 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class WebhookNotifier(Notifier):
    """``URL``, ``SECRET`` (optional), ``HEADERS``, ``TIMEOUT`` (10 s), ``RETRIES`` (2)."""

    REQUIRED = ('URL',)

    @property
    def retries(self) -> int:
        """Attempts after the first."""
        return int(self.option('RETRIES', 2))

    def body(self, outcome: Outcome, text: str) -> bytes:
        """The JSON sent: the outcome's fields, the text, and when it was sent."""
        payload = {**outcome.as_dict(), 'text': text, 'sent_at': datetime.now(timezone.utc).isoformat()}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()

    def headers(self, body: bytes) -> dict[str, str]:
        """``HEADERS``, the content type, and the signature when a ``SECRET`` is set."""
        found = {str(name): str(value) for name, value in (self.option('HEADERS') or {}).items()}
        found['Content-Type'] = 'application/json'
        secret = str(self.option('SECRET') or '')
        if secret:
            found[SIGNATURE_HEADER] = signature(secret, body)
        return found

    def _post(self, body: bytes, headers: Mapping[str, str]) -> Any:
        import httpx2  # noqa: PLC0415 - the [webhook] extra

        redact.hide(self.option('URL'))
        with httpx2.Client(timeout=float(self.option('TIMEOUT', 10))) as client:
            return client.post(str(self.option('URL')), content=body, headers=dict(headers))

    def send(self, outcome: Outcome, text: str) -> bool:
        """POST the outcome; never raises, never logs the URL, the body or the secret."""
        try:
            body = self.body(outcome, text)
            headers = self.headers(body)
        except Exception as error:  # noqa: BLE001 - a notifier outage must not cost the backup
            logger.error('Webhook body not built', extra={'ob_error': type(error).__name__})  # noqa: TRY400
            return False
        failure: dict[str, Any] = {}
        attempt = 0
        for attempt in range(self.retries + 1):
            if attempt:
                time.sleep(_BACKOFF * 2 ** (attempt - 1))
            try:
                response = self._post(body, headers)
            except Exception as error:  # noqa: BLE001 - retried, then logged by class only
                failure = {'ob_error': type(error).__name__}
                continue
            if response.is_success:
                return True
            failure = {'ob_status': response.status_code}
            if not _retried(response.status_code):
                break
        logger.error('Webhook refused or unreachable', extra={**failure, 'ob_attempts': attempt + 1})
        return False

    def check(self) -> list[checks.CheckMessage]:
        """E010: an http(s) ``URL``, a positive ``TIMEOUT``, ``RETRIES`` of 0 or more, ``HEADERS`` a mapping."""
        found = super().check()
        url = str(self.option('URL') or '').strip()
        if url and not url.startswith(('https://', 'http://')):
            found.append(
                checks.Error(f"{self.label}['URL'] is not an http:// or https:// URL.", id='offsite_backup.E010')
            )
        timeout = self.option('TIMEOUT', 10)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            found.append(
                checks.Error(
                    f"{self.label}['TIMEOUT'] is {timeout!r}, not a positive number of seconds.",
                    id='offsite_backup.E010',
                )
            )
        retries = self.option('RETRIES', 2)
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            found.append(
                checks.Error(
                    f"{self.label}['RETRIES'] is {retries!r}, not a whole number of 0 or more.",
                    id='offsite_backup.E010',
                )
            )
        if not isinstance(self.option('HEADERS', {}), Mapping):
            found.append(
                checks.Error(f"{self.label}['HEADERS'] is not a mapping of names to values.", id='offsite_backup.E010')
            )
        return found
