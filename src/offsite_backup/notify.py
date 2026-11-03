"""Say an outcome through every configured notifier, without ever costing the backup.

A test run must never reach a real chat: when silenced (by ``offsite_backup.testing``,
which the pytest plugin and the test runner turn on for the whole run), only notifiers
that cannot reach the network are called, and captures still see every message.
"""

import contextlib
import logging
import os
from collections.abc import Callable, Iterator
from typing import Any

from django.utils.module_loading import import_string

from offsite_backup import conf
from offsite_backup.notifiers.base import Notifier
from offsite_backup.outcome import Outcome
from offsite_backup.registry import build

__all__ = ('SILENCE_ENV', 'capture', 'formatter', 'notifiers', 'notify', 'silence', 'silence_process', 'silenced')

logger = logging.getLogger('offsite_backup')

_state: dict[str, Any] = {'silenced': 0, 'captures': []}


#: set by the test runner in the parent process; spawned and forkserver workers inherit the
#: environment and nothing else, so this is what silences them whatever hook was missed
SILENCE_ENV = 'OFFSITE_BACKUP_SILENCE'


def silenced() -> bool:
    """Whether network notifiers are switched off in this process."""
    return bool(_state['silenced']) or os.environ.get(SILENCE_ENV) == '1'


def silence_process() -> None:
    """Silence this process for the rest of its life (a test worker; a ``with`` would be collected)."""
    _state['silenced'] += 1


@contextlib.contextmanager
def silence() -> Iterator[None]:
    """No notifier that reaches the network is called inside the block."""
    _state['silenced'] += 1
    try:
        yield
    finally:
        _state['silenced'] -= 1


@contextlib.contextmanager
def capture() -> Iterator[list[tuple[Outcome, str]]]:
    """Collect every ``(outcome, text)`` said inside the block, whatever is configured."""
    found: list[tuple[Outcome, str]] = []
    _state['captures'].append(found)
    try:
        yield found
    finally:
        _state['captures'].remove(found)


def formatter(settings: conf.Settings) -> Callable[[Outcome], str]:
    """``MESSAGES``: a callable's path, or a module's path holding ``format_outcome``."""
    path = str(settings['MESSAGES'])
    try:
        found = import_string(path)
    except ImportError:
        import importlib  # noqa: PLC0415 - a module path, not an attribute's

        found = importlib.import_module(path)
    if not callable(found):
        found = found.format_outcome
    return found  # type: ignore[no-any-return]


def notifiers(settings: conf.Settings) -> list[Notifier]:
    """Every ``NOTIFY`` block built; a block that does not build is logged and skipped."""
    built: list[Notifier] = []
    for index, block in enumerate(settings['NOTIFY'] or []):
        try:
            built.append(build(block, Notifier, label=conf.label('NOTIFY', index)))
        except Exception as error:  # noqa: BLE001, PERF203 - checks report it; a pass must not die of it
            logger.error('Notifier not built', extra={'ob_notifier': index, 'ob_error': str(error)})  # noqa: TRY400
    return built


def notify(outcome: Outcome, settings: conf.Settings | None = None) -> int:
    """Format and send; answer how many notifiers delivered. Never raises."""
    try:
        settings = settings or conf.read()
        text = formatter(settings)(outcome)
    except Exception as error:  # noqa: BLE001 - a message that cannot be written must not hide the outcome
        logger.error('Backup message not formatted', extra={'ob_error': repr(error)[:500]})  # noqa: TRY400
        return 0
    for found in _state['captures']:
        found.append((outcome, text))
    delivered = 0
    for notifier in notifiers(settings):
        if silenced() and notifier.REACHES_NETWORK:
            continue
        try:
            delivered += bool(notifier.send(outcome, text))
        except Exception as error:  # noqa: BLE001 - a notifier outage must not cost the backup
            logger.error('Notifier raised', extra={'ob_notifier': notifier.label, 'ob_error': type(error).__name__})  # noqa: TRY400
    return delivered
