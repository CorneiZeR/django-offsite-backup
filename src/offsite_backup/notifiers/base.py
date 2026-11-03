"""Notifiers: where an outcome is said. A notifier never raises and never costs the backup."""

from offsite_backup.backend import Backend
from offsite_backup.outcome import Outcome

__all__ = ('Notifier',)


class Notifier(Backend):
    """Sends one message. Unknown third-party notifiers are assumed to reach the network."""

    REACHES_NETWORK = True

    def send(self, outcome: Outcome, text: str) -> bool:
        """Say ``text``; ``True`` when it was delivered (or handed over). Must not raise."""
        raise NotImplementedError
