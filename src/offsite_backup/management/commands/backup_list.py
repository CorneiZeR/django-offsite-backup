"""``manage.py backup_list``: what the store holds, per kind — nights, depth, newest lock."""

import json
from collections import defaultdict
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from offsite_backup import conf, naming
from offsite_backup.exceptions import BackupError
from offsite_backup.pass_ import components


class Command(BaseCommand):
    """List backups by kind."""

    help = 'List what the store holds under PREFIX: per kind, nights, objects, bytes and the newest lock.'

    def add_arguments(self, parser: CommandParser) -> None:
        """``--kind``, ``--store``, ``--json``."""
        parser.add_argument('--kind', action='append', help='Only this kind (db, media, config, db-<alias>, ...).')
        parser.add_argument('--store', help='The store of this NAME (default: the first store).')
        parser.add_argument('--json', action='store_true', help='Machine-readable output.')

    def handle(self, *args: Any, **options: Any) -> None:  # noqa: C901 - one table, read in one place
        """Read the store and print."""
        if not conf.enabled():
            self.stdout.write('backup_list: not configured on this machine, nothing to do')
            return
        try:
            parts = components()
            prefix = parts.settings.prefix
            store = parts.store_named(options['store'])
            stored = store.versions(f'{prefix}/')
        except BackupError as error:
            raise CommandError(f'backup_list: {error}') from error
        kinds: dict[str, dict[str, Any]] = defaultdict(
            lambda: {'objects': 0, 'bytes': 0, 'nights': set(), 'partial': 0, 'newest': '', 'newest_key': ''}
        )
        for key, held in stored.items():
            current = next((version for version in held if version.latest and not version.marker), None)
            name = naming.parse(key)
            if current is None or name is None:
                continue
            kind = key.removeprefix(f'{prefix}/').rsplit('/', 1)[0]
            if options['kind'] and kind not in options['kind']:
                continue
            entry = kinds[kind]
            entry['objects'] += 1
            entry['bytes'] += current.size
            entry['partial'] += name.marker == naming.WITHOUT_MEDIA
            if name.marker != naming.WITHOUT_MEDIA:
                entry['nights'].add(name.night.isoformat())
            if name.stamp > entry['newest']:
                entry['newest'], entry['newest_key'] = name.stamp, key
        rows = []
        for kind in sorted(kinds):
            entry = kinds[kind]
            locked = None
            if store.LOCKS and entry['newest_key']:
                held = stored[entry['newest_key']]
                current = next(version for version in held if version.latest and not version.marker)
                until = store.locked_until(entry['newest_key'], current)
                locked = until.isoformat() if until else None
            nights = sorted(entry['nights'])
            rows.append(
                {
                    'kind': kind,
                    'objects': entry['objects'],
                    'bytes': entry['bytes'],
                    'nights': len(nights),
                    'oldest_night': nights[0] if nights else None,
                    'newest': entry['newest'],
                    'partial_objects': entry['partial'],
                    'newest_locked_until': locked,
                }
            )
        if options['json']:
            self.stdout.write(json.dumps({'prefix': prefix, 'store': store.name, 'kinds': rows}, indent=2))
            return
        self.stdout.write(f'{prefix}/' + (f'  (store {store.name})' if len(parts.stores) > 1 else ''))
        for row in rows:
            self.stdout.write(
                f'  {row["kind"]}/: {row["nights"]} nights since {row["oldest_night"] or "-"}, '
                f'{row["objects"]} objects, {row["bytes"] / 1024**2:.1f} MB, newest {row["newest"]}'
                + (f', locked until {row["newest_locked_until"]}' if row['newest_locked_until'] else '')
                + (f', {row["partial_objects"]} without media' if row['partial_objects'] else '')
            )
