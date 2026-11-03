"""The ``OFFSITE_BACKUP`` setting: defaults, the on/off decision, and what is missing.

Three states, never confused:

* **off** — the setting is absent, ``ENABLED`` is false, or nothing that signals a
  backup destination is set (a development machine holds no key; not giving it one is
  the enforcement). Commands say "nothing to do" and exit 0.
* **partly configured** — something says "back up" and something it needs is empty.
  Refused by name (E001), never read as "off": a production machine that lost one
  variable must not stop backing up in silence.
* **on** — every backend built, every required option present.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from offsite_backup.backend import Backend
from offsite_backup.exceptions import BackendError, MisconfiguredError, NotConfiguredError

__all__ = (
    'DEFAULTS',
    'SETTINGS_NAME',
    'Settings',
    'enabled',
    'label',
    'load',
    'problems',
    'read',
)

SETTINGS_NAME = 'OFFSITE_BACKUP'

DEFAULTS: dict[str, Any] = {
    # None: on when a store or encryption option that names a destination is set
    'ENABLED': None,
    'PREFIX': '',
    'ENV': '',
    'NAME': '',
    'STORE': None,
    'ENCRYPTION': None,
    'DATABASES': None,
    'MEDIA': [],
    'CONFIG': None,
    'COMPRESS': ['zstd', '-19', '--long=27', '-T0', '-q'],
    'DECOMPRESS': ['zstd', '-d', '-q', '--long=31'],
    'RETENTION': {'KEEP_NIGHTS': 14},
    'CHECK': {'STALE_HOURS': 26},
    'NOTIFY': [],
    'MESSAGES': 'offsite_backup.messages.default',
    'GUARD': {'ENABLED': True, 'CACHE': 'default', 'RUNNING_SECONDS': 3600, 'DONE_SECONDS': 26 * 3600},
    'LAST_SUCCESS_FILE': '',
    'WORKDIR': '',
}

#: nested blocks whose own defaults merge key by key rather than being replaced whole
_MERGED = ('RETENTION', 'CHECK', 'GUARD')


def label(*path: str | int) -> str:
    """``OFFSITE_BACKUP['STORE']['BUCKET']`` — how every message names a setting."""
    return SETTINGS_NAME + ''.join(f'[{part!r}]' for part in path)


@dataclass(frozen=True)
class Settings:
    """The merged setting, read once per pass."""

    values: dict[str, Any] = field(default_factory=dict)
    present: bool = False

    def __getitem__(self, key: str) -> Any:
        """One top-level key, defaulted."""
        return self.values[key]

    @property
    def prefix(self) -> str:
        """The key prefix every object lives under, without surrounding slashes."""
        return str(self.values['PREFIX'] or '').strip().strip('/')

    @property
    def media_blocks(self) -> list[Mapping[str, Any]]:
        """``MEDIA`` as a list, accepting a single block for convenience."""
        media = self.values['MEDIA'] or []
        return [media] if isinstance(media, Mapping) else list(media)

    @property
    def keep_nights(self) -> int:
        """Whole nights kept per kind."""
        return int(self.values['RETENTION']['KEEP_NIGHTS'])

    @property
    def stale_hours(self) -> float:
        """How old the newest whole backup may be before the morning check calls it missing."""
        return float(self.values['CHECK']['STALE_HOURS'])


def read() -> Settings:
    """The setting merged over the defaults, without validating anything."""
    raw = getattr(settings, SETTINGS_NAME, None)
    values = copy.deepcopy(DEFAULTS)
    if not raw:
        return Settings(values=values, present=False)
    for key, value in dict(raw).items():
        if key in _MERGED and isinstance(value, Mapping):
            values[key] = {**values[key], **value}
        else:
            values[key] = value
    return Settings(values=values, present=True)


def _blocks(conf: Settings) -> list[tuple[str, Mapping[str, Any], type[Backend]]]:
    """Every configured backend block with its label and base class."""
    from offsite_backup.config.base import ConfigSource  # noqa: PLC0415 - base classes only
    from offsite_backup.databases.base import DatabaseSource  # noqa: PLC0415
    from offsite_backup.encryption.base import Sealer  # noqa: PLC0415
    from offsite_backup.media.base import MediaSource  # noqa: PLC0415
    from offsite_backup.stores.base import Store  # noqa: PLC0415

    found: list[tuple[str, Mapping[str, Any], type[Backend]]] = []
    families: tuple[tuple[str, type[Backend]], ...] = (
        ('STORE', Store),
        ('ENCRYPTION', Sealer),
        ('DATABASES', DatabaseSource),
        ('CONFIG', ConfigSource),
    )
    for key, base in families:
        block = conf[key]
        if isinstance(block, Mapping):
            found.append((label(key), block, base))
    found.extend((label('MEDIA', index), block, MediaSource) for index, block in enumerate(conf.media_blocks))
    return found


def _empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False


def enabled(conf: Settings | None = None) -> bool:
    """Whether this machine backs up at all — see the module docstring for the three states."""
    from offsite_backup.registry import backend_class  # noqa: PLC0415 - imports backend modules

    conf = conf or read()
    if not conf.present:
        return False
    explicit = conf['ENABLED']
    if explicit is not None:
        return bool(explicit)
    for name, block, base in _blocks(conf):
        if base.__name__ not in {'Store', 'Sealer'}:
            continue
        try:
            cls = backend_class(str(block.get('BACKEND') or ''), base, label=name)
        except BackendError:
            # a backend that does not resolve is a configuration, broken; E002 says how
            return True
        if any(not _empty(block.get(option)) for option in cls.SIGNALS):
            return True
    return False


def problems(conf: Settings | None = None) -> list[str]:
    """Every missing piece of an enabled configuration, each named as a setting."""
    from offsite_backup.registry import backend_class  # noqa: PLC0415 - imports backend modules

    conf = conf or read()
    missing: list[str] = []
    if not conf.prefix:
        missing.append(label('PREFIX'))
    missing.extend(
        label(key, 'BACKEND')
        for key in ('STORE', 'ENCRYPTION')
        if not isinstance(conf[key], Mapping) or _empty(conf[key].get('BACKEND'))
    )
    if not (conf['DATABASES'] or conf.media_blocks or conf['CONFIG']):
        missing.append(f'{label("DATABASES")}, {label("MEDIA")} or {label("CONFIG")} (nothing to back up)')
    from offsite_backup.notifiers.base import Notifier  # noqa: PLC0415 - base class only

    blocks = _blocks(conf)
    blocks += [(label('NOTIFY', index), block, Notifier) for index, block in enumerate(conf['NOTIFY'] or [])]
    for name, block, base in blocks:
        try:
            cls = backend_class(str(block.get('BACKEND') or ''), base, label=name)
        except BackendError:
            continue  # E002's to report
        missing.extend(f'{name}[{option!r}]' for option in cls.REQUIRED if _empty(block.get(option)))
    keep = conf['RETENTION'].get('KEEP_NIGHTS')
    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
        missing.append(f'{label("RETENTION", "KEEP_NIGHTS")} (a positive integer, got {keep!r})')
    return missing


def load() -> Settings:
    """The setting of an enabled machine, or a refusal: off, or partly configured by name."""
    conf = read()
    if not enabled(conf):
        msg = f'{SETTINGS_NAME} is not configured on this machine, so there is nothing to back up.'
        raise NotConfiguredError(msg)
    missing = problems(conf)
    if missing:
        msg = 'Backups are configured by halves; these are unset: ' + ', '.join(missing)
        raise MisconfiguredError(msg)
    return conf
