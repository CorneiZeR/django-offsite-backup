"""Telegram through the Bot API over HTTPS (``httpx2``, the ``[telegram]`` extra).

A topic id typed wrong makes Telegram refuse the message; it is logged and nothing is
posted to the main thread instead. A 429 is retried once after ``retry_after``.
"""

import logging
import time
from typing import Any

from offsite_backup.notifiers.base import ChatNotifier
from offsite_backup.outcome import Outcome

__all__ = ('TelegramNotifier',)

logger = logging.getLogger('offsite_backup')

#: the longest wait a 429 is honoured for; longer and the message is dropped, logged
_MAX_RETRY_AFTER = 30


class TelegramNotifier(ChatNotifier):
    """``TOKEN``, ``CHAT_ID``, ``TOPIC_ID`` (optional), ``TIMEOUT`` (10 s), ``API_URL``."""

    REQUIRED = ('TOKEN', 'CHAT_ID')

    def _post(self, payload: dict[str, Any]) -> Any:
        import httpx2  # noqa: PLC0415 - the [telegram] extra

        base = str(self.option('API_URL', 'https://api.telegram.org')).rstrip('/')
        url = f'{base}/bot{self.option("TOKEN")}/sendMessage'
        with httpx2.Client(timeout=float(self.option('TIMEOUT', 10))) as client:
            return client.post(url, json=payload)

    def send(self, outcome: Outcome, text: str) -> bool:
        """``sendMessage`` with HTML; never raises, never logs the token or the text."""
        payload = self.message(text)
        try:
            for attempt in range(2):
                response = self._post(payload)
                if response.status_code == 429 and attempt == 0:  # noqa: PLR2004 - Too Many Requests
                    wait = int(response.json().get('parameters', {}).get('retry_after', 1))
                    if wait <= _MAX_RETRY_AFTER:
                        time.sleep(wait)
                        continue
                if response.is_success:
                    return True
                description = response.json().get('description', '') if response.content else ''
                logger.error(
                    'Telegram refused the backup message',
                    extra={'ob_status': response.status_code, 'ob_description': str(description)[:200]},
                )
                return False
        except Exception as error:  # noqa: BLE001 - a notifier outage must not cost the backup
            # the class only: the URL, and so the token, is in the message of several errors
            logger.error('Telegram unreachable', extra={'ob_error': type(error).__name__})  # noqa: TRY400
        return False
