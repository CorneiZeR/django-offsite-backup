"""Through the project's own bot, with `django-aiogram` (the ``[aiogram]`` extra).

``DELIVERY: 'direct'`` (default) calls ``bot.send_raw``: synchronous where no bot loop runs
(a worker, a one-off container), so the worker needs the bot token. ``'queue'`` calls
``bot.send``, which hands the message to the queue a bot container consumes — no token in
the worker, django-aiogram's rate limiting and event log apply, and ``True`` means
"queued": delivery then depends on the bot container, which logs a refusal itself.
"""

import logging
from typing import Any

from django.core import checks
from django.utils.module_loading import import_string

from offsite_backup.notifiers.base import ChatNotifier
from offsite_backup.outcome import Outcome

__all__ = ('DELIVERIES', 'TopicNotifier')

logger = logging.getLogger('offsite_backup')

DELIVERIES = ('direct', 'queue')


class TopicNotifier(ChatNotifier):
    """``CHAT_ID``, ``TOPIC_ID``, ``DELIVERY``, and ``BOT`` (an alias) or ``BOT_FACTORY`` (a callable's path)."""

    @property
    def delivery(self) -> str:
        """``direct`` or ``queue``."""
        return str(self.option('DELIVERY', 'direct'))

    def _bot(self) -> Any:
        factory = self.option('BOT_FACTORY')
        if factory:
            # any object with send_raw/send: another bot wrapper, or an older django-aiogram
            return import_string(str(factory))()
        alias = self.option('BOT')
        if alias:
            from django_aiogram.runtime.registry import bots  # noqa: PLC0415 - the [aiogram] extra

            return bots[alias]
        from django_aiogram import bot  # noqa: PLC0415 - the [aiogram] extra

        return bot

    def send(self, outcome: Outcome, text: str) -> bool:
        """``send_raw`` or ``send`` with ``send_message``; never raises."""
        try:
            bot = self._bot()
            method = bot.send if self.delivery == 'queue' else bot.send_raw
            method('send_message', **self.message(text))
        except Exception as error:  # noqa: BLE001 - a notifier outage must not cost the backup
            logger.error('Backup message not sent through the bot', extra={'ob_error': type(error).__name__})  # noqa: TRY400
            return False
        return True

    def check(self) -> list[checks.CheckMessage]:
        """``DELIVERY`` is known; ``BOT_FACTORY`` imports and is callable."""
        found = super().check()
        if self.delivery not in DELIVERIES:
            found.append(
                checks.Error(
                    f"{self.label}['DELIVERY'] is {self.delivery!r}; expected one of {', '.join(DELIVERIES)}.",
                    id='offsite_backup.E001',
                )
            )
        factory = self.option('BOT_FACTORY')
        if factory:
            try:
                resolved = import_string(str(factory))
            except ImportError as error:
                resolved = None
                found.append(
                    checks.Error(
                        f"{self.label}['BOT_FACTORY'] is {factory!r}, which cannot be imported: {error}",
                        id='offsite_backup.E002',
                    )
                )
            if resolved is not None and not callable(resolved):
                found.append(
                    checks.Error(
                        f"{self.label}['BOT_FACTORY'] is {factory!r}, which is not callable.", id='offsite_backup.E002'
                    )
                )
        return found
