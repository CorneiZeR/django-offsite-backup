"""A notifier that writes to the ``offsite_backup`` logger — always available."""

import logging

from offsite_backup.notifiers.base import Notifier
from offsite_backup.outcome import WRITTEN, Outcome

__all__ = ('LogNotifier',)

logger = logging.getLogger('offsite_backup')


class LogNotifier(Notifier):
    """INFO for a written backup, ERROR for anything else; the text goes in ``extra``."""

    REACHES_NETWORK = False

    def send(self, outcome: Outcome, text: str) -> bool:
        """Log it."""
        level = logging.INFO if outcome.kind == WRITTEN else logging.ERROR
        logger.log(level, 'Backup %s', outcome.kind, extra={'ob_prefix': outcome.prefix, 'ob_text': text})
        return True
