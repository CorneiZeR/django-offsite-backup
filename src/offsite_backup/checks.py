"""System checks. Codes are stable; the Troubleshooting page explains each one.

Network-touching checks (the bucket's lock configuration, the database server's version)
are registered as deploy checks, so a plain ``manage.py`` never reaches S3 or the database.
So are the paths a pass reads (E009, W005): a web or migration container has no reason to
mount them, and a warning every deploy prints there is one people learn to ignore.
Nothing is checked on a machine where backups are off.

``settings_findings`` and ``deploy_findings`` are the checks themselves and need no Django:
the command line runs them as ``offsite-backup checks``. ``check_settings`` and
``check_deploy`` hand them to Django's checks framework, adding what only a Django project
has — the once-per-night guard's cache or database, and a test run's notifiers.
"""

import shutil
import sys
from collections.abc import Sequence
from typing import Any

from offsite_backup import conf
from offsite_backup import findings as checks
from offsite_backup.backend import Backend
from offsite_backup.exceptions import BackendError
from offsite_backup.registry import backend_class, missing_extra

__all__ = (
    'backends',
    'check_deploy',
    'check_settings',
    'deploy_findings',
    'register_all',
    'settings_findings',
)

_TAG = 'offsite_backup'


def _under_test() -> bool:
    return 'pytest' in sys.modules or sys.argv[1:2] == ['test']


def _django(found: list[checks.CheckMessage]) -> list[Any]:
    """Each finding as Django's check message, so ``manage.py check`` levels and silences it."""
    from django.core.checks import CheckMessage  # noqa: PLC0415 - the Django layer only

    return [CheckMessage(item.level, item.msg, hint=item.hint, id=item.id) for item in found]


def backends(settings: conf.Settings) -> tuple[list[Backend], list[checks.CheckMessage]]:
    """Every configured backend built, and an E002 for each that cannot be."""
    from offsite_backup.notifiers.base import Notifier  # noqa: PLC0415 - base classes only

    found: list[Backend] = []
    errors: list[checks.CheckMessage] = []
    blocks = conf._blocks(settings)  # noqa: SLF001 - the module's own table
    blocks += [(conf.label('NOTIFY', index), block, Notifier) for index, block in enumerate(settings['NOTIFY'] or [])]
    for name, block, base in blocks:
        path = str(block.get('BACKEND') or '').strip()
        install = missing_extra(path)
        if install:
            errors.append(
                checks.Error(
                    f"{name}['BACKEND'] is {path.rsplit('.', 1)[-1]}, but its driver is not installed.",
                    hint=install,
                    id='offsite_backup.E002',
                )
            )
            continue
        try:
            cls = backend_class(path, base, label=name)
        except BackendError as error:
            errors.append(checks.Error(str(error), id='offsite_backup.E002'))
            continue
        found.append(cls({key: value for key, value in block.items() if key != 'BACKEND'}, label=name))
    return found, errors


def settings_findings(settings: conf.Settings) -> list[checks.CheckMessage]:
    """E001-E003, E007, E008, E010, W001, W002: everything that needs neither the network, a mount nor Django."""
    if not conf.enabled(settings):
        return []
    messages: list[checks.CheckMessage] = []
    missing = conf.problems(settings)
    if missing:
        messages.append(
            checks.Error(
                'Backups are configured by halves; these are unset: ' + ', '.join(missing),
                hint='Set every one, or leave the store credentials empty to turn backups off on this machine.',
                id='offsite_backup.E001',
            )
        )
    built, errors = backends(settings)
    messages += errors
    for backend in built:
        messages += backend.check()
    # the decompressor only matters where something is compressed
    compressors = (settings['COMPRESS'], settings['DECOMPRESS']) if settings['COMPRESS'] else ()
    for binary in sorted({command[0] for command in compressors if command}):
        if not shutil.which(binary):
            messages.append(
                checks.Error(
                    f'`{binary}` is not on PATH, and COMPRESS or DECOMPRESS runs it.',
                    hint=f'Install {binary} in the image that runs the backup.',
                    id='offsite_backup.E003',
                )
            )
    return messages


def check_settings(app_configs: Sequence[Any] | None = None, **kwargs: Any) -> list[Any]:
    """``settings_findings``, plus the guard (E001, W003) and a test run's notifiers (W004)."""
    settings = conf.read()
    if not conf.enabled(settings):
        return []
    messages = settings_findings(settings)
    guard = settings['GUARD']
    if guard.get('ENABLED', True):
        messages += _guard(guard)
    if _under_test() and settings['NOTIFY']:
        from offsite_backup.notify import silenced  # noqa: PLC0415

        if not silenced():
            messages.append(
                checks.Warning(
                    'Notifiers are configured in a test run and not silenced.',
                    hint='Use offsite_backup.testing.OffsiteBackupTestRunner (pytest is covered by the plugin), '
                    "and keep production notifier tokens out of the CI test job's variables.",
                    id='offsite_backup.W004',
                )
            )
    return _django(messages)


def _guard(guard: dict[str, Any]) -> list[checks.CheckMessage]:
    from django.conf import settings  # noqa: PLC0415

    backend = guard.get('BACKEND', 'cache')
    if backend == 'database':
        alias = str(guard.get('DATABASE', 'default'))
        engine = str(settings.DATABASES.get(alias, {}).get('ENGINE', ''))
        if 'postgresql' not in engine:
            return [
                checks.Error(
                    f"GUARD['DATABASE'] is {alias!r}, which is not a PostgreSQL database in DATABASES.",
                    hint='The database guard takes a PostgreSQL advisory lock; use the cache backend otherwise.',
                    id='offsite_backup.E001',
                )
            ]
        return []
    if backend != 'cache':
        return [
            checks.Error(
                f"GUARD['BACKEND'] is {backend!r}; expected 'cache' or 'database'.",
                id='offsite_backup.E001',
            )
        ]
    return _guard_cache(str(guard.get('CACHE', 'default')))


def _guard_cache(alias: str) -> list[checks.CheckMessage]:
    from django.conf import settings  # noqa: PLC0415

    backend = str(settings.CACHES.get(alias, {}).get('BACKEND', ''))
    if alias not in settings.CACHES:
        return [
            checks.Error(
                f"GUARD['CACHE'] is {alias!r}, which CACHES does not define.",
                id='offsite_backup.E001',
            )
        ]
    if backend.endswith(('LocMemCache', 'DummyCache')):
        return [
            checks.Warning(
                f'The once-per-night guard uses cache {alias!r} ({backend.rsplit(".", 1)[-1]}), '
                'which workers do not share.',
                hint='Point GUARD["CACHE"] at a shared cache (Redis, Memcached, database), '
                'or two schedulers can both run tonight.',
                id='offsite_backup.W003',
            )
        ]
    return []


def deploy_findings(settings: conf.Settings) -> list[checks.CheckMessage]:
    """E004-E006, E009, E011, W005: the bucket's lock, ``pg_dump`` against the server, paths, the drill's target."""
    if not conf.enabled(settings) or conf.problems(settings):
        return []
    built, _ = backends(settings)
    messages: list[checks.CheckMessage] = []
    for backend in built:
        messages += backend.deploy_check()
    return messages + _drill_target(settings, built)


def _drill_target(settings: conf.Settings, built: list[Backend]) -> list[checks.CheckMessage]:
    """E011: ``DRILL['DATABASE']`` names no database, or the live one — the drill would refuse to run."""
    from offsite_backup.databases.base import DatabaseSource  # noqa: PLC0415 - base class only
    from offsite_backup.drill import refuse_live  # noqa: PLC0415 - imports the pass

    target = str(settings['DRILL'].get('DATABASE') or '').strip()
    source = next((backend for backend in built if isinstance(backend, DatabaseSource)), None)
    if not target or source is None:
        return []
    try:
        refuse_live(source, target, source.targets())
    except Exception as error:  # noqa: BLE001 - every way the target is wrong is this check's finding
        return [
            checks.Error(
                str(error),
                hint='Point DRILL["DATABASE"] at a throwaway database on another server, or another database name.',
                id='offsite_backup.E011',
            )
        ]
    return []


def check_deploy(app_configs: Sequence[Any] | None = None, **kwargs: Any) -> list[Any]:
    """``deploy_findings``, for ``manage.py check --deploy``."""
    return _django(deploy_findings(conf.read()))


def register_all() -> None:
    """Register both checks (idempotent: Django's registry is a set)."""
    from django.core.checks import register  # noqa: PLC0415 - the Django layer only

    register(check_settings, _TAG)
    register(check_deploy, _TAG, deploy=True)
