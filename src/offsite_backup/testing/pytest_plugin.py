"""Silence network notifiers for every pytest run in a project with this package installed.

Registered under ``pytest11``, so it covers a run whose environment holds a production token:
notifiers stay silent from the first test to the last.
"""

import pytest

__all__ = ('pytest_configure', 'pytest_unconfigure')

_held: list[object] = []


def pytest_configure(config: pytest.Config) -> None:
    """Enter ``silence()`` for the session; imports nothing that needs Django settings."""
    from offsite_backup.notify import silence  # noqa: PLC0415 - kept off pytest's own import path

    block = silence()
    block.__enter__()
    _held.append(block)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Leave it."""
    while _held:
        block = _held.pop()
        block.__exit__(None, None, None)  # type: ignore[attr-defined]
