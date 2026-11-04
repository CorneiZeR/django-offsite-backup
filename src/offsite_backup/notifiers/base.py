"""Notifiers: where an outcome is said. A notifier never raises and never costs the backup."""

from typing import Any, ClassVar

from offsite_backup import findings as checks
from offsite_backup.backend import Backend
from offsite_backup.outcome import Outcome

__all__ = ('ChatNotifier', 'Notifier', 'unset_id')


def unset_id(value: Any) -> bool:
    """``None``, blank and ``0`` are "not set" — what ``int(os.environ.get('X') or 0)`` produces."""
    return value is None or str(value).strip() in {'', '0'}


class Notifier(Backend):
    """Sends one message. Unknown third-party notifiers are assumed to reach the network."""

    REACHES_NETWORK = True

    def send(self, outcome: Outcome, text: str) -> bool:
        """Say ``text``; ``True`` when it was delivered (or handed over). Must not raise."""
        raise NotImplementedError


class ChatNotifier(Notifier):
    """A Telegram chat, optionally a topic in it: ``CHAT_ID`` and ``TOPIC_ID``, validated once here."""

    REQUIRED: ClassVar[tuple[str, ...]] = ('CHAT_ID',)

    def message(self, text: str) -> dict[str, Any]:
        """``sendMessage`` arguments: HTML, no link preview, the topic when one is set."""
        found: dict[str, Any] = {
            'chat_id': self.option('CHAT_ID'),
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
        }
        topic = self.option('TOPIC_ID')
        if not unset_id(topic):
            found['message_thread_id'] = int(topic)
        return found

    def check(self) -> list[checks.CheckMessage]:
        """E010: ``TOPIC_ID`` is an integer when set — one check for both Telegram notifiers."""
        found = super().check()
        topic = self.option('TOPIC_ID')
        if not unset_id(topic) and not str(topic).strip().lstrip('-').isdigit():
            found.append(
                checks.Error(f"{self.label}['TOPIC_ID'] is {topic!r}, not an integer.", id='offsite_backup.E010')
            )
        return found
