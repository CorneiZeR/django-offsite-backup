"""One backup pass, and the one-off database backup.

Order (each step's reason is a failure it prevents):

1. Every database, one at a time: dump -> compress -> encrypt -> upload (lock read back)
   -> delete the local file. A database that fails is recorded and the pass goes on:
   one database that cannot be dumped does not cost the others, the media or the config
   their night.
2. Unless the pass is without media: every media source, one volume at a time, each
   followed by its manifest.
3. The configuration archive LAST: a config object for a stamp means every part before
   it went up, and the morning check relies on that.
4. Only after everything is uploaded, rotation — making room before the new backup exists
   is how a bad night becomes an empty bucket.
5. Depth, the message, the last-success marker.
6. Any database or store that failed raises now, naming every one, after the rest went up.

With several stores every object goes to each of them. A store that refuses one is
recorded, written to no more and not rotated tonight (its night is lost; rotating it would
make room for a backup it does not hold), while the others carry the pass on — which then
fails by the store's name: one store's outage is never a silent partial.
"""

import logging
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from offsite_backup import conf, naming
from offsite_backup.config.base import ConfigSource
from offsite_backup.databases.base import DatabaseSource
from offsite_backup.encryption.base import Sealer
from offsite_backup.exceptions import BackupFailedError, MisconfiguredError
from offsite_backup.media.base import MANIFEST_SUFFIX, ManifestRow, MediaSource, write_manifest
from offsite_backup.notify import notify
from offsite_backup.outcome import FAILED, WRITTEN, Outcome, Summary
from offsite_backup.registry import build
from offsite_backup.rotation import depth, rotate
from offsite_backup.sealing import Sealing
from offsite_backup.stores.base import Store

__all__ = ('KINDS', 'Components', 'back_up_database', 'components', 'keep_apart', 'run')

logger = logging.getLogger('offsite_backup')

#: what ``only`` may name
KINDS = ('db', 'media', 'config')


@dataclass
class Components:
    """Every backend a pass uses, built from the settings."""

    settings: conf.Settings
    stores: list[Store]
    sealer: Sealer
    databases: DatabaseSource | None = None
    media: list[MediaSource] = field(default_factory=list)
    config: ConfigSource | None = None

    @property
    def store(self) -> Store:
        """The first store: where a restore, a listing and the guard read unless told otherwise."""
        return self.stores[0]

    def store_named(self, name: str | None) -> Store:
        """The store whose ``NAME`` is ``name``; the first one for none."""
        if not name:
            return self.store
        for store in self.stores:
            if store.name == name:
                return store
        known = ', '.join(repr(store.name) for store in self.stores if store.name) or 'none'
        msg = f'no store is named {name!r} (named: {known})'
        raise MisconfiguredError(msg)

    def sealing(self, extra_recipients: Sequence[str] = ()) -> Sealing:
        """Compression and encryption as configured."""
        return Sealing(self.sealer, self.settings['COMPRESS'] or (), tuple(extra_recipients))


def components(settings: conf.Settings | None = None) -> Components:
    """Build every backend of an enabled, complete configuration (raises otherwise)."""
    settings = settings or conf.load()
    found = Components(
        settings=settings,
        stores=[build(block, Store, label=name) for name, block in settings.store_blocks],
        sealer=build(settings['ENCRYPTION'], Sealer, label=conf.label('ENCRYPTION')),
    )
    if settings['DATABASES']:
        found.databases = build(settings['DATABASES'], DatabaseSource, label=conf.label('DATABASES'))
    found.media = [
        build(block, MediaSource, label=conf.label('MEDIA', index)) for index, block in enumerate(settings.media_blocks)
    ]
    if settings['CONFIG']:
        found.config = build(settings['CONFIG'], ConfigSource, label=conf.label('CONFIG'))
    for backend in (found.databases, found.config, *found.media):
        if backend is not None:
            backend.strict = bool(settings['STRICT'])
    return found


def _error_text(error: BaseException) -> str:
    return f'{type(error).__name__}: {error}'


def _mark_success(settings: conf.Settings, at: datetime) -> None:
    path = str(settings['LAST_SUCCESS_FILE'] or '')
    if not path:
        return
    try:
        Path(path).write_text(at.isoformat() + '\n')
    except OSError as error:
        logger.warning('Last-success marker not written', extra={'ob_path': path, 'ob_error': str(error)})


@dataclass
class _Pass:
    """The state of one pass while its parts go up."""

    parts: Components
    summary: Summary
    selected: set[str]
    with_media: bool
    partial: bool
    moment: datetime
    here: Path = Path()
    kinds: list[str] = field(default_factory=list)
    healthy: list[Store] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Every store is written to until it refuses an object."""
        self.healthy = list(self.parts.stores)
        self.summary.stores = [store.name for store in self.parts.stores]

    def ship(self, path: Path, kind: str) -> None:
        """Upload one sealed file under its kind to every store still standing, then delete it locally."""
        key = naming.join(self.summary.prefix, kind, path.name)
        for store in list(self.healthy):
            try:
                wrote = store.put(path, key, now=self.moment)
            except Exception as error:
                if len(self.parts.stores) == 1:
                    raise
                logger.exception('Store not written; left out of this pass', extra={'ob_store': store.name})
                self.summary.failed_stores[store.name] = _error_text(error)
                self.healthy.remove(store)
                continue
            self.summary.uploaded.append(replace(wrote, store=store.name))
        if not self.healthy:
            named = '; '.join(f'{name}: {text}' for name, text in self.summary.failed_stores.items())
            msg = f'no store took the backup — {named}'
            raise BackupFailedError(msg)
        path.unlink()

    def databases(self, sealing: Sealing) -> None:
        """Every target; a failing one is recorded and the rest go on."""
        source = self.parts.databases
        if source is None or 'db' not in self.selected:
            return
        try:
            aliases = source.targets()
        except Exception as error:  # the media and the config still go up tonight
            logger.exception('Databases not listed')
            self.summary.failed['(targets)'] = _error_text(error)
            return
        for alias in aliases:
            kind = naming.db_kind(alias)
            self.kinds.append(kind)
            name = naming.object_name(self.summary.stamp, source.suffix(), partial=self.partial)
            try:
                self.ship(source.dump(alias, self.here / name, sealing), kind)
            except Exception as error:  # recorded; raised at the end
                logger.exception('Database not backed up', extra={'ob_alias': alias})
                self.summary.failed[alias] = _error_text(error)
                (self.here / name).unlink(missing_ok=True)

    def media(self, sealing: Sealing) -> None:
        """Every source, one volume at a time, each followed by its manifest."""
        if not self.with_media:
            return
        stamp = self.summary.stamp
        for source in self.parts.media:
            kind = naming.media_kind(source.name)
            self.kinds.append(kind)

            def path_for(number: int, source: MediaSource = source) -> Path:
                return self.here / naming.object_name(stamp, source.suffix(), number=number)

            def ship(path: Path, rows: list[ManifestRow], kind: str = kind) -> None:
                number = naming.parse(path.name).number  # type: ignore[union-attr]
                manifest = self.here / naming.object_name(stamp, MANIFEST_SUFFIX, number=number)
                self.ship(path, kind)
                self.ship(write_manifest(rows, manifest, sealing, volume=path.name), kind)

            source.seal_volumes(path_for, sealing, ship)

    def config(self, sealing: Sealing) -> None:
        """The configuration archive — last."""
        source = self.parts.config
        if source is None or 'config' not in self.selected:
            return
        self.kinds.append(naming.CONFIG)
        name = naming.object_name(self.summary.stamp, source.suffix(), partial=self.partial)
        self.ship(source.archive(self.here / name, sealing), naming.CONFIG)

    def rotate(self) -> None:
        """Rotate every kind this pass wrote to, in every store that holds tonight, then read the depth."""
        prefix = self.summary.prefix
        default = self.parts.settings.keep_nights
        for store in self.healthy:
            for kind in self.kinds:
                result = rotate(store, naming.folder(prefix, kind), store.keep_nights(default), now=self.moment)
                self.summary.removed += result.removed
                self.summary.held += result.held
        depth_kind = next((kind for kind in self.kinds if kind.startswith('db')), naming.CONFIG)
        self.summary.depth = depth(self.healthy[0], naming.folder(prefix, depth_kind))

    def failure(self) -> str:
        """Every database and store that did not take tonight, by name; blank for none."""
        lines = []
        if self.summary.failed_stores:
            named = '; '.join(f'{name}: {text}' for name, text in self.summary.failed_stores.items())
            lines.append(f'{len(self.summary.failed_stores)} store(s) not written, the others hold the pass — {named}')
        if self.summary.failed:
            named = '; '.join(f'{alias}: {text}' for alias, text in self.summary.failed.items())
            lines.append(f'{len(self.summary.failed)} database(s) not backed up, the rest went up — {named}')
        return '\n'.join(lines)


def run(
    *,
    media: bool = True,
    reason: str = 'nightly',
    only: Sequence[str] | None = None,
    now: datetime | None = None,
) -> Summary:
    """One pass now. Raises when not configured, on any failure, and when any database or store failed."""
    settings = conf.load()
    selected = set(only or KINDS)
    unknown = selected - set(KINDS)
    if unknown:
        msg = f'only= takes {", ".join(KINDS)}; got {", ".join(sorted(unknown))}'
        raise ValueError(msg)
    parts = components(settings)
    with_media = media and 'media' in selected
    moment = now or datetime.now(timezone.utc)
    summary = Summary(
        stamp=naming.stamp(moment), prefix=settings.prefix, reason=reason, media=with_media or not parts.media
    )
    state = _Pass(parts, summary, selected, with_media, partial=bool(parts.media) and not with_media, moment=moment)
    started = time.monotonic()
    base = Outcome(
        kind=WRITTEN, prefix=settings.prefix, env=str(settings['ENV']), name=str(settings['NAME']), reason=reason
    )
    try:
        sealing = parts.sealing()
        with tempfile.TemporaryDirectory(prefix='offsite-backup-', dir=settings['WORKDIR'] or None) as workspace:
            state.here = Path(workspace)
            state.databases(sealing)
            state.media(sealing)
            state.config(sealing)
        state.rotate()
    except Exception as error:
        summary.seconds = time.monotonic() - started
        notify(replace(base, kind=FAILED, error=_error_text(error), summary=summary), settings)
        raise
    summary.seconds = time.monotonic() - started
    message = state.failure()
    if message:
        notify(replace(base, kind=FAILED, error=message, summary=summary), settings)
        raise BackupFailedError(message)
    _mark_success(settings, moment)
    logger.info(
        'Backup written',
        extra={'ob_prefix': settings.prefix, 'ob_objects': summary.objects, 'ob_bytes': summary.bytes},
    )
    notify(replace(base, kind=WRITTEN, summary=summary), settings)
    return summary


def back_up_database(
    alias: str,
    *,
    extra_recipients: Sequence[str] = (),
    keep_apart: bool = False,
    now: datetime | None = None,
) -> str:
    """One database off the machine now, outside the nightly pass; answers with its key.

    For the moment a database is about to go (removed, put to sleep): raises when backups
    are not configured — or when any store did not take it — so a removal that cannot be
    backed up does not happen. Uploaded under ``db-<alias>/``; nothing rotates that folder
    while the database is not a target. ``extra_recipients`` seal it to more keys (e.g. one
    the server holds, so it can restore it itself); ``keep_apart`` writes it under
    ``<PREFIX>/released/`` where no rotation reads.
    """
    parts = components()
    if parts.databases is None:
        msg = f'{conf.label("DATABASES")} is not configured, so no database can be backed up.'
        raise MisconfiguredError(msg)
    moment = now or datetime.now(timezone.utc)
    base = parts.settings.prefix + (f'/{naming.RELEASED}' if keep_apart else '')
    name = naming.object_name(naming.stamp(moment), parts.databases.suffix())
    key = naming.join(base, naming.db_kind(alias), name)
    with tempfile.TemporaryDirectory(prefix='offsite-backup-', dir=parts.settings['WORKDIR'] or None) as workspace:
        path = parts.databases.dump(alias, Path(workspace) / name, parts.sealing(extra_recipients))
        for store in parts.stores:
            store.put(path, key, now=moment)
    logger.info('Database backed up', extra={'ob_alias': alias, 'ob_key': key})
    return key


def keep_apart(key: str, *, now: datetime | None = None) -> str:
    """Copy one stored backup under ``<PREFIX>/released/`` in every store, locked afresh; answer the new key.

    The original stays where the rotation that owns it will take it; the copy is the one
    nothing rotates — so a reused alias never mixes two databases' history.
    """
    parts = components()
    prefix = parts.settings.prefix
    released = f'{prefix}/{naming.RELEASED}/'
    if key.startswith(released):
        return key
    if not key.startswith(f'{prefix}/'):
        msg = f'{key!r} is not under {prefix}/'
        raise ValueError(msg)
    target = released + key.removeprefix(f'{prefix}/')
    for store in parts.stores:
        store.copy(key, target, now=now)
    return target
