"""``manage.py backup_list``: what the store holds, per kind — nights, depth, newest lock."""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from offsite_backup import conf, listing
from offsite_backup.exceptions import BackupError
from offsite_backup.pass_ import components


class Command(BaseCommand):
    """List backups by kind."""

    help = 'List what the store holds under PREFIX: per kind, nights, objects, bytes and the newest lock.'

    def add_arguments(self, parser: CommandParser) -> None:
        """``--kind``, ``--json``."""
        parser.add_argument('--kind', action='append', help='Only this kind (db, media, config, db-<alias>, ...).')
        parser.add_argument('--json', action='store_true', help='Machine-readable output.')

    def handle(self, *args: Any, **options: Any) -> None:
        """Read the store and print."""
        if not conf.enabled():
            self.stdout.write('backup_list: not configured on this machine, nothing to do')
            return
        try:
            parts = components()
            table = listing.rows(parts, options['kind'] or ())
        except BackupError as error:
            raise CommandError(f'backup_list: {error}') from error
        prefix = parts.settings.prefix
        if options['json']:
            self.stdout.write(json.dumps({'prefix': prefix, 'kinds': table}, indent=2))
            return
        for line in listing.lines(prefix, table):
            self.stdout.write(line)
