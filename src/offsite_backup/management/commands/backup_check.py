"""``manage.py backup_check``: the morning check; non-zero when a backup is missing or unchecked."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from offsite_backup import conf
from offsite_backup.check import check_last_night
from offsite_backup.exceptions import BackupError


class Command(BaseCommand):
    """Fail when the newest whole backup is older than STALE_HOURS."""

    help = 'Check that a whole backup was written within CHECK["STALE_HOURS"]; exit non-zero otherwise.'

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the check."""
        if not conf.enabled():
            self.stdout.write('backup_check: not configured on this machine, nothing to do')
            return
        try:
            newest = check_last_night()
        except BackupError as error:
            raise CommandError(f'backup_check: {error}') from error
        when = f'{newest:%Y-%m-%d %H:%M} UTC' if newest else 'n/a'
        self.stdout.write(f'backup_check: ok, newest whole backup {when}')
