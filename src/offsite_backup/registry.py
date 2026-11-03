"""Backends named by dotted path, resolved when first needed, never guessed.

Nothing is inferred from what happens to be installed: two drivers present would be
ambiguous, and one present would make a typo look like a working configuration.
"""

import importlib.util
from collections.abc import Mapping
from typing import Any, TypeVar

from django.utils.module_loading import import_string

from offsite_backup.backend import Backend
from offsite_backup.exceptions import BackendError

__all__ = ('SHIPPED', 'backend_class', 'build', 'missing_extra')

#: dotted path -> (module that must import, extra that provides it); readable without
#: importing the backend, so a check names the missing extra where the import would fail
SHIPPED: dict[str, tuple[str, str]] = {
    'offsite_backup.stores.s3.S3Store': ('boto3', 's3'),
    'offsite_backup.media.s3.S3Source': ('boto3', 's3'),
    'offsite_backup.notifiers.telegram.TelegramNotifier': ('httpx2', 'telegram'),
    'offsite_backup.notifiers.django_aiogram.TopicNotifier': ('django_aiogram', 'aiogram'),
}

B = TypeVar('B', bound=Backend)


def missing_extra(path: str) -> str | None:
    """The install line for a shipped backend whose driver is absent, else ``None``."""
    if path not in SHIPPED:
        return None
    module, extra = SHIPPED[path]
    if importlib.util.find_spec(module) is not None:
        return None
    return f'pip install "django-offsite-backup[{extra}]"'


def backend_class(path: str, base: type[B], *, label: str) -> type[B]:
    """Import ``path`` and insist it is a subclass of ``base``."""
    if not path:
        msg = f"{label}['BACKEND'] is empty."
        raise BackendError(msg)
    try:
        found = import_string(path)
    except (ImportError, ValueError) as error:
        msg = f"{label}['BACKEND'] is {path!r}, which cannot be imported: {error}"
        raise BackendError(msg) from error
    if not (isinstance(found, type) and issubclass(found, base)):
        msg = f"{label}['BACKEND'] is {path!r}, which is not a {base.__name__} subclass."
        raise BackendError(msg)
    return found


def build(block: Mapping[str, Any], base: type[B], *, label: str) -> B:
    """Construct the backend a settings block names, refusing a missing driver by its extra."""
    path = str(block.get('BACKEND') or '').strip()
    install = missing_extra(path)
    if install:
        msg = f"{label}['BACKEND'] is {path.rsplit('.', 1)[-1]}, but its driver is not installed — {install}"
        raise BackendError(msg)
    cls = backend_class(path, base, label=label)
    options = {key: value for key, value in block.items() if key != 'BACKEND'}
    return cls(options, label=label)
