"""``manage.py backup_status``: the newest whole backup's age per kind, for a monitor; exits 1 when stale."""

import json
import sys
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from offsite_backup import conf, metrics


class Command(BaseCommand):
    """Answer how old each kind's newest whole backup is — silently: nothing is notified."""

    help = (
        'Print the age of the newest whole backup per kind; exit 0 when every kind is fresh, 1 when one is '
        'older than STALE_HOURS or absent, 2 when the store cannot be read. --prometheus writes a textfile.'
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """``--json``, ``--prometheus``, ``--stale-hours``."""
        parser.add_argument('--json', action='store_true', help='Machine-readable output.')
        parser.add_argument(
            '--prometheus', metavar='FILE', help='Also write the metrics there, atomically (a textfile collector).'
        )
        parser.add_argument('--stale-hours', type=float, help='How old is too old (default: CHECK["STALE_HOURS"], 26).')

    def handle(self, *args: Any, **options: Any) -> None:
        """Ask the store, print, exit with the answer."""
        if not conf.enabled():
            self.stdout.write('backup_status: not configured on this machine, nothing to do')
            return
        code, found = metrics.status(stale_hours=options['stale_hours'], textfile=options['prometheus'])
        if options['json']:
            self.stdout.write(json.dumps(metrics.as_json(found), indent=2))
        elif not found.up:
            self.stderr.write(f'backup_status: the store could not be read — {found.error}')
        else:
            for line in metrics.lines(found):
                self.stdout.write(line)
        if code:
            sys.exit(code)
