"""Encrypted, immutable off-site backups for Django projects.

Importing the package loads nothing but Django's settings machinery: every backend,
driver and the pass itself are imported when first used (PEP 562 ``__getattr__``).
"""

from typing import TYPE_CHECKING, Any

__version__ = '0.2.0'

__all__ = (
    'BackupError',
    'NotConfiguredError',
    '__version__',
    'back_up_database',
    'check_last_night',
    'keep_apart',
    'restore_database',
    'run',
    'run_nightly',
)

_EXPORTS = {
    'run': 'offsite_backup.pass_',
    'back_up_database': 'offsite_backup.pass_',
    'keep_apart': 'offsite_backup.pass_',
    'run_nightly': 'offsite_backup.guard',
    'check_last_night': 'offsite_backup.check',
    'restore_database': 'offsite_backup.restore',
    'BackupError': 'offsite_backup.exceptions',
    'NotConfiguredError': 'offsite_backup.exceptions',
}

if TYPE_CHECKING:
    from offsite_backup.check import check_last_night
    from offsite_backup.exceptions import BackupError, NotConfiguredError
    from offsite_backup.guard import run_nightly
    from offsite_backup.pass_ import back_up_database, keep_apart, run
    from offsite_backup.restore import restore_database


def __getattr__(name: str) -> Any:
    """Resolve a public name on first use, so ``import offsite_backup`` stays cheap."""
    module = _EXPORTS.get(name)
    if module is None:
        msg = f'module {__name__!r} has no attribute {name!r}'
        raise AttributeError(msg)
    import importlib  # noqa: PLC0415 - only when an export is first asked for

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value
