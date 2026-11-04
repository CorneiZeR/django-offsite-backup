"""``manage.py backup_drill``: restore the newest whole backup into a throwaway database, verify, report."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from offsite_backup import conf
from offsite_backup.drill import run_drill
from offsite_backup.exceptions import BackupError
from offsite_backup.restore import IDENTITY_ENV


class Command(BaseCommand):
    """The restore drill."""

    help = (
        "Restore the newest whole backup into DRILL['DATABASE'] (never a live database), count every table, "
        'open the media and configuration archives, report through the notifiers and clean up. '
        f"The private key comes from --identity FILE, DRILL['IDENTITY_FILE'] or ${IDENTITY_ENV}."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """``--identity``, ``--database``, ``--without-media``."""
        parser.add_argument(
            '--identity', help=f"The age identity file; default: DRILL['IDENTITY_FILE'], ${IDENTITY_ENV}."
        )
        parser.add_argument('--database', help="The throwaway alias to restore into; default: DRILL['DATABASE'].")
        parser.add_argument(
            '--without-media', action='store_true', help='Leave the media volumes unopened (they are the slow part).'
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the drill; non-zero on any failure, which the message names."""
        if not conf.enabled():
            msg = 'backup_drill: backups are not configured on this machine'
            raise CommandError(msg)
        try:
            report = run_drill(
                identity=options['identity'],
                database=options['database'],
                media=False if options['without_media'] else None,
            )
        except BackupError as error:
            raise CommandError(f'backup_drill: {error}') from error
        self.stdout.write(
            f'backup_drill: {report.stamp} came back — {len(report.databases)} database(s) '
            f'into {report.target!r}, {len(report.archives)} archive(s) opened'
        )
