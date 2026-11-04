"""The restore drill: prove the newest whole backup comes back, then clean up after it.

A backup nobody has restored is a hope. The drill does what a person does by hand once a
month: download the newest whole pass and decrypt it with an identity kept for the purpose;
restore every database into a throwaway database the project names — never a live one —
and count every table there (and live, to compare); decrypt and list every media volume
and the configuration archive; say the outcome through the notifiers; empty the throwaway
database and remove the workspace, whatever happened.

One database that does not come back does not stop the others: each failure is recorded by
the object's key and step, and the drill raises at the end naming every one, as a pass does.
Rows are compared, not required to match — production kept writing after the backup. A
table the live database has and the restore lacks is named in the message (a migration
since the pass, or a hole in the backup) without failing the drill.
"""

import logging
import tarfile
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from offsite_backup import conf, naming
from offsite_backup.databases.base import DatabaseSource
from offsite_backup.exceptions import BackupError, DrillFailedError, MisconfiguredError
from offsite_backup.media.base import MANIFEST_SUFFIX
from offsite_backup.notify import notify
from offsite_backup.outcome import DRILL_FAILED, DRILLED, Drill, DrilledArchive, DrilledDatabase, Outcome
from offsite_backup.pass_ import Components, components
from offsite_backup.restore import _kind, _plain_name, _unseal, identity_file, keys_of, latest_stamp

__all__ = ('database_alias', 'refuse_live', 'run_drill')

logger = logging.getLogger('offsite_backup')


def database_alias(kind: str) -> str:
    """The alias a ``db``/``db-<alias>`` folder holds — ``naming.db_kind`` read backwards."""
    return 'default' if kind == 'db' else kind.removeprefix('db-')


def refuse_live(source: DatabaseSource, target: str, live: list[str]) -> None:
    """Raise unless ``target`` is a database apart from every one in ``live``."""
    source.connection_settings(target)  # a target that names nothing is refused by its own name
    for alias in live:
        try:
            same = source.same_database(target, alias)
        except MisconfiguredError:
            continue  # an alias that resolves to nothing is not a live database
        if same:
            msg = (
                f'{conf.label("DRILL", "DATABASE")} is {target!r}, which is the live database {alias!r}: '
                'the drill restores into a throwaway database only'
            )
            raise MisconfiguredError(msg)


def _is_manifest(key: str) -> bool:
    name = naming.parse(key)
    return name is not None and name.suffix == MANIFEST_SUFFIX


def _error_text(error: BaseException) -> str:
    return f'{type(error).__name__}: {error}'


def _counted(source: DatabaseSource, alias: str) -> dict[str, int]:
    """``tables(alias)``, refusing a restore that left no table at all."""
    found = source.tables(alias)
    if not found:
        msg = 'the restored database has no tables'
        raise BackupError(msg)
    return found


class _Run:
    """One drill's state while its parts come back."""

    def __init__(self, parts: Components, report: Drill, here: Path, secret: str) -> None:
        self.parts = parts
        self.report = report
        self.here = here
        self.secret = secret
        self.compare = bool(parts.settings['DRILL'].get('COMPARE_LIVE', True))

    def database(self, key: str) -> None:
        """Download, verify, restore into the target, count — the step that failed is named."""
        source = self.parts.databases
        if source is None:  # keys of a db kind with DATABASES unset: refused before
            return
        alias = database_alias(_kind(self.parts.settings.prefix, key))
        target = self.report.target
        plain = self.here / _plain_name(key)
        step = 'download and decrypt'
        try:
            _unseal(self.parts, key, self.secret, plain)
            step = 'verify'
            source.verify(plain)
            step = f'empty {target!r}'
            source.clear(target)
            step = f'restore into {target!r}'
            source.restore(plain, target)
            step = f'count {target!r}'
            restored = _counted(source, target)
            entry = DrilledDatabase(alias=alias, key=key, tables=len(restored), rows=sum(restored.values()))
            if self.compare:
                self.live(source, alias, entry, restored)
            self.report.databases.append(entry)
        except Exception as error:  # recorded by name; raised at the end
            logger.exception('Database not restored by the drill', extra={'ob_key': key, 'ob_step': step})
            self.report.failed[key] = f'{step}: {_error_text(error)}'
        finally:
            plain.unlink(missing_ok=True)

    @staticmethod
    def live(source: DatabaseSource, alias: str, entry: DrilledDatabase, restored: dict[str, int]) -> None:
        """The live database's counts beside the restored ones; one that cannot be counted is logged, not failed."""
        try:
            live = source.tables(alias)
        except Exception as error:  # noqa: BLE001 - a tenant gone since the pass, a read-only role
            logger.warning('Live database not counted', extra={'ob_alias': alias, 'ob_error': type(error).__name__})
            return
        entry.live_tables = len(live)
        entry.live_rows = sum(live.values())
        entry.missing = sorted(set(live) - set(restored))

    def archive(self, key: str) -> None:
        """Download, decrypt and list one tar: every member read, so a cut-short volume fails here."""
        kind = _kind(self.parts.settings.prefix, key)
        plain = self.here / _plain_name(key)
        step = 'download and decrypt'
        try:
            _unseal(self.parts, key, self.secret, plain)
            step = 'list'
            entry = DrilledArchive(kind=kind, key=key)
            with tarfile.open(plain, mode='r:') as tar:
                for member in tar:
                    if member.isfile():
                        entry.files += 1
                        entry.bytes += member.size
            self.report.archives.append(entry)
        except Exception as error:  # recorded by name; raised at the end
            logger.exception('Archive not opened by the drill', extra={'ob_key': key, 'ob_step': step})
            self.report.failed[key] = f'{step}: {_error_text(error)}'
        finally:
            plain.unlink(missing_ok=True)


def _prepare(parts: Components, report: Drill, *, with_media: bool) -> tuple[list[str], list[str]]:
    """The newest whole pass's database keys and archive keys; refuses a drill that could reach a live database."""
    prefix = parts.settings.prefix
    if parts.databases is not None:
        # before the store is read: a drill aimed at production stops at once, whatever is stored
        if not report.target:
            msg = f'{conf.label("DRILL", "DATABASE")} is not set: the drill restores into a throwaway database it names'
            raise MisconfiguredError(msg)
        refuse_live(parts.databases, report.target, parts.databases.targets())
    report.stamp = latest_stamp(parts)
    keys = keys_of(parts, report.stamp)
    databases = [key for key in keys if _kind(prefix, key).startswith('db')]
    archives = [
        key
        for key in keys
        if key not in databases
        and (with_media or not _kind(prefix, key).startswith(naming.media_kind('')))
        # a manifest is not a tar: it describes the volume beside it, which is what is opened
        and not _is_manifest(key)
    ]
    if databases:
        if parts.databases is None:
            msg = f'the backup holds databases and {conf.label("DATABASES")} is not configured to restore them'
            raise MisconfiguredError(msg)
        # the aliases the pass wrote, too: a database no longer a target may still be live
        refuse_live(parts.databases, report.target, sorted({database_alias(_kind(prefix, key)) for key in databases}))
    return databases, archives


def run_drill(
    *,
    identity: Path | str | None = None,
    identity_text: str | None = None,
    database: str | None = None,
    media: bool | None = None,
    now: datetime | None = None,
) -> Drill:
    """Restore the newest whole backup into ``DRILL['DATABASE']`` (or ``database``), verify, report, clean up.

    The identity is a file (``identity``, else ``DRILL['IDENTITY_FILE']``), its text, or
    ``AGE_IDENTITY``. Raises ``DrillFailedError`` naming every part that did not come back,
    after saying so through the notifiers.
    """
    settings = conf.load()
    parts = components(settings)
    block = settings['DRILL']
    report = Drill(stamp='', prefix=settings.prefix, target=(database or str(block.get('DATABASE') or '')).strip())
    with_media = bool(block.get('MEDIA', True)) if media is None else media
    identity = identity or (str(block.get('IDENTITY_FILE') or '').strip() or None)
    base = Outcome(
        kind=DRILLED,
        prefix=settings.prefix,
        env=str(settings['ENV']),
        name=str(settings['NAME']),
        reason='drill',
        now=now or datetime.now(timezone.utc),
        drill=report,
    )
    started = time.monotonic()
    try:
        databases, archives = _prepare(parts, report, with_media=with_media)
        workspace = tempfile.TemporaryDirectory(prefix='offsite-drill-', dir=settings['WORKDIR'] or None)
        with workspace as here, identity_file(identity, identity_text) as secret:
            run = _Run(parts, report, Path(here), secret)
            try:
                for key in databases:
                    run.database(key)
            finally:
                if databases and parts.databases is not None:
                    try:
                        parts.databases.clear(report.target)
                    except Exception as error:  # the restored data must not stay; said loudly
                        logger.exception('Drill database not emptied', extra={'ob_alias': report.target})
                        report.failed[f'empty {report.target!r} afterwards'] = _error_text(error)
            for key in archives:
                run.archive(key)
    except Exception as error:
        report.seconds = time.monotonic() - started
        notify(replace(base, kind=DRILL_FAILED, error=_error_text(error)), settings)
        raise
    report.seconds = time.monotonic() - started
    if report.failed:
        named = '; '.join(f'{name}: {text}' for name, text in report.failed.items())
        message = f'{len(report.failed)} part(s) of {report.stamp} did not come back — {named}'
        notify(replace(base, kind=DRILL_FAILED, error=message), settings)
        raise DrillFailedError(message)
    logger.info('Restore drill passed', extra={'ob_prefix': settings.prefix, 'ob_stamp': report.stamp})
    notify(base, settings)
    return report
