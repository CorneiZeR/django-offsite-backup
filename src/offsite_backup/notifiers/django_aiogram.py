"""Through the project's own bot, with `django-aiogram` (the ``[aiogram]`` extra).

``send_raw`` is synchronous where no bot loop runs (a worker, a one-off container). In a
process whose bot loop runs, it only schedules — ``True`` then means "handed over".
Whether a refused send raises follows django-aiogram's own ``RAISE_EXCEPTION``.
"""

import logging
from typing import Any

from offsite_backup.notifiers.base import Notifier
from offsite_backup.outcome import Outcome

__all__ = ('TopicNotifier',)

logger = logging.getLogger('offsite_backup')


class TopicNotifier(Notifier):
    """``CHAT_ID``, ``TOPIC_ID`` (optional), ``BOT`` (an alias of a configured bot; default bot if absent)."""

    REQUIRED = ('CHAT_ID',)

    def _bot(self) -> Any:
        alias = self.option('BOT')
        if alias:
            from django_aiogram.runtime.registry import bots  # noqa: PLC0415 - the [aiogram] extra

            return bots[alias]
        from django_aiogram import bot  # noqa: PLC0415 - the [aiogram] extra

        return bot

    def send(self, outcome: Outcome, text: str) -> bool:
        """``bot.send_raw('send_message', ...)``; never raises."""
        kwargs: dict[str, Any] = {
            'chat_id': self.option('CHAT_ID'),
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }
        if self.option('TOPIC_ID') not in (None, ''):
            kwargs['message_thread_id'] = int(self.option('TOPIC_ID'))
        try:
            self._bot().send_raw('send_message', **kwargs)
        except Exception as error:  # noqa: BLE001 - a notifier outage must not cost the backup
            logger.error('Backup message not sent through the bot', extra={'ob_error': type(error).__name__})  # noqa: TRY400
            return False
        return True
