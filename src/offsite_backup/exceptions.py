"""One error family for the package."""

__all__ = (
    'BackendError',
    'BackupError',
    'BackupFailedError',
    'BackupMissingError',
    'BackupUncheckedError',
    'BackupUnlockedError',
    'MisconfiguredError',
    'NotConfiguredError',
    'PipelineError',
    'SourceMissingError',
)


class BackupError(Exception):
    """Base class of every error this package raises on purpose."""


class NotConfiguredError(BackupError):
    """Backups are off on this machine; an explicit request for one cannot be honoured."""


class MisconfiguredError(BackupError):
    """Backups are configured by halves; the message names every missing piece."""


class BackendError(MisconfiguredError):
    """A backend path that does not import, is not the right kind, or lacks its extra."""


class SourceMissingError(BackupError):
    """A required path, file or source was not there at pass time; the message names it."""


class PipelineError(BackupError):
    """A stage of a subprocess pipeline exited non-zero."""


class BackupUnlockedError(BackupError):
    """An object landed without the lock it was sent with."""


class BackupFailedError(BackupError):
    """A pass finished with parts that did not go up; the message names them."""


class BackupMissingError(BackupError):
    """The morning check found no whole backup young enough."""


class BackupUncheckedError(BackupError):
    """The morning check could not read the store."""
