"""What every pluggable backend shares: its options block, its requirements, its checks."""

import shutil
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from offsite_backup import findings as checks

__all__ = ('Backend',)


class Backend:
    """A backend built from its own settings block (``BACKEND`` plus options)."""

    #: options that must be non-empty once backups are on; E001 names each one missing
    REQUIRED: ClassVar[tuple[str, ...]] = ()
    #: options whose presence means "this machine is meant to back up" (see ``conf.enabled``)
    SIGNALS: ClassVar[tuple[str, ...]] = ()
    #: executables this backend runs; E003 names each one absent from PATH
    BINARIES: ClassVar[tuple[str, ...]] = ()
    #: a backend that cannot reach the network may run under a test suite (see ``testing``)
    REACHES_NETWORK: ClassVar[bool] = False
    #: ``OFFSITE_BACKUP['STRICT']``, set on every backend a pass builds
    strict: bool = False

    @property
    def required(self) -> bool:
        """``REQUIRED: True`` on the block, or ``STRICT`` for the whole pass."""
        return self.strict or self.option('REQUIRED') is True

    def __init__(self, options: Mapping[str, Any], *, label: str = '') -> None:
        """Keep the options block and the label checks and errors name it by."""
        self.options = dict(options)
        self.label = label or type(self).__name__

    def option(self, name: str, default: Any = None) -> Any:
        """One option, with ``default`` for absent *and* for ``None``."""
        value = self.options.get(name)
        return default if value is None else value

    def strings(self, name: str, default: Sequence[str] = ()) -> list[str]:
        """A list option with blank items dropped; a single string is a one-item list."""
        value = self.option(name, list(default))
        values = [value] if isinstance(value, str) else list(value)
        return [str(item).strip() for item in values if str(item).strip()]

    def check(self) -> list[checks.CheckMessage]:
        """Local, offline checks: binaries present, options well formed."""
        return [
            checks.Error(
                f'{self.label} runs `{binary}`, which is not on PATH.',
                hint=f'Install {binary} in the image that runs the backup.',
                id='offsite_backup.E003',
            )
            for binary in self.BINARIES
            if shutil.which(binary) is None
        ]

    def deploy_check(self) -> list[checks.CheckMessage]:
        """Checks that touch the network or a database; run with ``check --deploy`` only."""
        return []
