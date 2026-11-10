"""The ``OFFSITE_BACKUP`` setting: defaults, the on/off decision, and what is missing.

Three states, never confused:

* **off** — the setting is absent, ``ENABLED`` is false, or nothing that signals a
  backup destination is set (a development machine holds no key; not giving it one is
  the enforcement). Commands say "nothing to do" and exit 0.
* **partly configured** — something says "back up" and something it needs is empty.
  Refused by name (E001), never read as "off": a production machine that lost one
  variable must not stop backing up in silence.
* **on** — every backend built, every required option present.

Where the setting comes from: Django's ``settings.OFFSITE_BACKUP``, or — on a server with no
Django project — the mapping the command line read from its TOML file and handed to
``configure()``. Everything after reading is the same for both.
"""

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from offsite_backup.backend import Backend
from offsite_backup.exceptions import BackendError, MisconfiguredError, NotConfiguredError

__all__ = (
    'DEFAULTS',
    'SETTINGS_NAME',
    'Settings',
    'base_dir',
    'configure',
    'django_setting',
    'enabled',
    'label',
    'load',
    'problems',
    'read',
    'standalone',
    'with_paths',
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
    # what backup_offsite says when --reason is not given
    'DEFAULT_REASON': 'manual',
    # a missing config path, an unreadable file or an empty required source fails the pass
    'STRICT': False,
    'WORKDIR': '',
    # the restore drill: the throwaway database it restores into, and what it opens
    'DRILL': {'DATABASE': '', 'IDENTITY_FILE': '', 'MEDIA': True, 'COMPARE_LIVE': True},
}

#: nested blocks whose own defaults merge key by key rather than being replaced whole
_MERGED = ('RETENTION', 'CHECK', 'GUARD', 'DRILL')

#: the setting handed over by ``configure()``; ``None`` reads Django's settings
_standalone: dict[str, Any] = {'values': None, 'base_dir': None}


def configure(values: Mapping[str, Any] | None, *, base_dir: Path | str | None = None) -> None:
    """Use ``values`` as the setting from now on instead of Django's; ``None`` goes back to Django.

    ``base_dir`` stands in for ``settings.BASE_DIR`` (what a relative ``RENDER_ENV`` is read
    against): the command line passes its configuration file's directory.
    """
    _standalone['values'] = None if values is None else dict(values)
    _standalone['base_dir'] = None if base_dir is None else Path(base_dir)


def standalone() -> bool:
    """Whether the setting came from ``configure()`` rather than from Django."""
    return _standalone['values'] is not None


def django_setting(name: str, default: Any = None) -> Any:
    """One of Django's settings, or ``default`` where there is no Django project to ask."""
    if standalone():
        return default
    try:
        from django.conf import settings  # noqa: PLC0415 - Django is an optional extra
    except ImportError:
        return default
    return getattr(settings, name, default)


def with_paths(values: Mapping[str, Any], paths: Sequence[Path | str]) -> dict[str, Any]:
    """The setting with ``paths`` added to ``CONFIG``'s ``PATHS``, or a ``PathsSource`` of them alone.

    What ``offsite-backup run --path`` and ``backup_offsite --path`` add to one pass: files that
    belong with tonight's configuration archive (a dump the deploy took, a secrets copy) and are
    not part of the setting. They are ``HANDED`` too: one missing, or a file under it that cannot
    be read or that ``EXCLUDE`` would leave out, fails the pass instead of being left out —
    whoever handed them over may delete them once the pass returns. The setting itself is not
    changed.
    """
    if not paths:
        return dict(values)
    block = dict(values.get('CONFIG') or {'BACKEND': 'offsite_backup.config.paths.PathsSource'})
    added = list(dict.fromkeys(str(path) for path in paths))
    listed = block.get('PATHS') or []
    base = [listed] if isinstance(listed, str) else [str(path) for path in listed]
    # one already listed is archived once, and still handed: its caller may delete it
    block['PATHS'] = [*base, *(path for path in added if path not in base)]
    handed = block.get('HANDED') or []
    block['HANDED'] = [*([handed] if isinstance(handed, str) else handed), *added]
    return {**values, 'CONFIG': block}


def base_dir() -> Path:
    """What relative paths in the setting are read against: ``configure(base_dir=)``, ``BASE_DIR``, or here."""
    if standalone():
        return _standalone['base_dir'] or Path()
    return Path(str(django_setting('BASE_DIR', '.')))


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
    def store_blocks(self) -> list[tuple[str, Mapping[str, Any]]]:
        """``STORE`` as ``(label, block)`` pairs: one block, or a list of them (several stores)."""
        store = self.values['STORE']
        if isinstance(store, Mapping):
            return [(label('STORE'), store)]
        if isinstance(store, (list, tuple)):
            return [(label('STORE', index), block) for index, block in enumerate(store) if isinstance(block, Mapping)]
        return []

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
    raw = _standalone['values'] if standalone() else django_setting(SETTINGS_NAME)
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

    found: list[tuple[str, Mapping[str, Any], type[Backend]]] = [
        (name, block, Store) for name, block in conf.store_blocks
    ]
    families: tuple[tuple[str, type[Backend]], ...] = (
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
    """Unset: ``None``, blank, ``0`` (``int(os.environ.get('X') or 0)``), or a list of only those."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, dict):
        return not value
    if isinstance(value, (list, tuple, set)):
        # [os.environ.get('X', '')] is [''] on a machine without X
        return all(_empty(item) for item in value)
    return False


def _store_names(stores: list[tuple[str, Mapping[str, Any]]]) -> list[str]:
    """Several stores: each needs a ``NAME`` of its own, which messages and ``--store`` use."""
    if len(stores) == 1:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for name, block in stores:
        given = str(block.get('NAME') or '').strip()
        if not given:
            found.append(f"{name}['NAME'] (several stores need a name each)")
        elif given in seen:
            found.append(f"{name}['NAME'] ({given!r} is taken by another store)")
        seen.add(given)
    return found


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
    stores = conf.store_blocks or [(label('STORE'), {})]
    missing.extend(f"{name}['BACKEND']" for name, block in stores if _empty(block.get('BACKEND')))
    if not isinstance(conf['ENCRYPTION'], Mapping) or _empty(conf['ENCRYPTION'].get('BACKEND')):
        missing.append(label('ENCRYPTION', 'BACKEND'))
    missing.extend(_store_names(stores))
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
