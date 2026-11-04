"""Secrets out of the HTTP client's own log: it writes every request's URL, and Telegram's carries the token."""

import logging

#: the loggers of the HTTP clients the notifiers use
_CLIENT_LOGGERS = ('httpx2', 'httpx')
_MASK = '<redacted>'


class _Redact(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.hidden: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.hidden:
            return True
        message = record.getMessage()
        redacted = message
        for value in self.hidden:
            redacted = redacted.replace(value, _MASK)
        if redacted != message:
            record.msg, record.args = redacted, ()
        return True


_FILTER = _Redact()


def hide(*values: object) -> None:
    """Never let these appear in the HTTP clients' log records, whoever configured the logging."""
    _FILTER.hidden.update(str(value) for value in values if value)
    for name in _CLIENT_LOGGERS:
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
