"""``manage.py backup_restore``: download, decrypt, verify — a stamp, one database or some media files."""

from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from offsite_backup import conf, naming
from offsite_backup.exceptions import BackupError
from offsite_backup.restore import IDENTITY_ENV, fetch, restore_database, restore_media


class Command(BaseCommand):
    """Bring a backup back."""

    help = (
        'Download and decrypt every object of a stamp into a directory, verifying database dumps; '
        '--database ALIAS for one database, --path for some media files; --apply loads one database. '
        f'The private key comes from --identity FILE or ${IDENTITY_ENV}.'
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """``--stamp``, ``--identity``, ``--into``, ``--kind``, ``--database``, ``--path``, ``--store``, ``--apply``."""
        parser.add_argument('--stamp', required=True, help='A stamp (2026-01-31T03-00Z), or "latest" (newest whole).')
        parser.add_argument('--identity', help=f'The age identity file; default: ${IDENTITY_ENV}.')
        parser.add_argument('--into', help='Directory to write into (default: ./restore-<stamp>).')
        parser.add_argument('--kind', action='append', help='Only this kind; repeat for several.')
        parser.add_argument(
            '--database',
            help="Only this database's dump; with --apply, the alias to load the backup into.",
        )
        parser.add_argument('--from-database', help='With --apply: whose backup to load (default: --database).')
        parser.add_argument(
            '--path',
            action='append',
            help='Only this media file, or everything under this folder; repeat for several. '
            'Downloads only the volumes whose manifest lists a match.',
        )
        parser.add_argument('--store', help='Read from the store of this NAME (default: the first store).')
        parser.add_argument('--apply', action='store_true', help='Load the database backup into --database.')
        parser.add_argument('--clean', action='store_true', help='With --apply: replace a non-empty database.')

    def handle(self, *args: Any, **options: Any) -> None:
        """Fetch, restore some media, or apply."""
        if not conf.enabled():
            msg = 'backup_restore: backups are not configured on this machine'
            raise CommandError(msg)
        if options['clean'] and not options['apply']:
            msg = '--clean only means something with --apply'
            raise CommandError(msg)
        if options['path'] and (options['apply'] or options['database']):
            msg = '--path restores media files; it does not combine with --database or --apply'
            raise CommandError(msg)
        try:
            if options['apply']:
                self._apply(options)
                return
            into = Path(options['into'] or f'restore-{options["stamp"]}')
            kinds = list(options['kind'] or ())
            if options['path']:
                for restored in restore_media(
                    options['stamp'],
                    options['path'],
                    into=into,
                    identity=options['identity'],
                    kinds=kinds,
                    store=options['store'],
                ):
                    self.stdout.write(f'{restored.path}' + ('  (verified)' if restored.verified else ''))
                return
            if options['database']:
                kinds.append(naming.db_kind(options['database']))
            for restored in fetch(
                options['stamp'], into=into, identity=options['identity'], kinds=kinds, store=options['store']
            ):
                self.stdout.write(f'{restored.path}' + ('  (verified)' if restored.verified else ''))
        except BackupError as error:
            raise CommandError(f'backup_restore: {error}') from error

    def _apply(self, options: dict[str, Any]) -> None:
        if not options['database']:
            msg = '--apply needs --database ALIAS'
            raise CommandError(msg)
        key = restore_database(
            options['stamp'],
            options['database'],
            identity=options['identity'],
            clean=options['clean'],
            source_alias=options['from_database'],
            store=options['store'],
        )
        self.stdout.write(f'backup_restore: {key} loaded into {options["database"]!r}')
