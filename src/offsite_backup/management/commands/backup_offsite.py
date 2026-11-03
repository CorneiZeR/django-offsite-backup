"""``manage.py backup_offsite``: one pass now; non-zero on failure, so a deploy can rely on it."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from offsite_backup import conf
from offsite_backup.exceptions import BackupError
from offsite_backup.pass_ import KINDS, run


class Command(BaseCommand):
    """Back up the databases, media and configuration off the machine."""

    help = 'Back up the databases, media and deployment configuration off the machine, now.'

    def add_arguments(self, parser: CommandParser) -> None:
        """``--without-media``, ``--reason``, ``--only``."""
        parser.add_argument(
            '--without-media',
            action='store_true',
            help='Databases and configuration only (the pass before a deploy); marked so it never counts as a night.',
        )
        parser.add_argument(
            '--reason',
            default=None,
            help='Why it runs, for the message: nightly, deploy, manual, or any word '
            "(default: OFFSITE_BACKUP['DEFAULT_REASON'], manual unless set).",
        )
        parser.add_argument(
            '--only',
            action='append',
            choices=KINDS,
            help='Back up only this part; repeat for several. A pass without media is marked partial.',
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the pass; say "nothing to do" and exit 0 where backups are off."""
        given = options['reason']
        if given is not None and not str(given).strip():
            msg = '--reason cannot be empty'
            raise CommandError(msg)
        if not conf.enabled():
            self.stdout.write('backup_offsite: not configured on this machine, nothing to do')
            return
        reason = str(given).strip() if given is not None else str(conf.read()['DEFAULT_REASON'])
        if given is None and options['without_media'] and reason != 'deploy':
            # a pass without media by hand is rare; a deploy that forgot the flag reads "By hand"
            self.stderr.write(
                f'backup_offsite: the reason is {reason!r}; before a deploy, pass --reason deploy '
                '(the message then says the deploy stops on failure)'
            )
        try:
            summary = run(media=not options['without_media'], reason=reason, only=options['only'])
        except BackupError as error:
            raise CommandError(f'backup_offsite: {error}') from error
        self.stdout.write(f'backup_offsite: {len(summary.uploaded)} objects, {summary.bytes} bytes up')
