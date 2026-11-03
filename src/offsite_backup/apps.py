"""The Django app: registers the system checks and imports nothing else."""

from django.apps import AppConfig

__all__ = ('OffsiteBackupConfig',)


class OffsiteBackupConfig(AppConfig):
    """Registers the checks; every backend stays unimported until a pass needs it."""

    name = 'offsite_backup'
    verbose_name = 'Off-site backup'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self) -> None:
        """Register the system checks."""
        from offsite_backup import checks  # noqa: PLC0415 - registration is the import's effect

        checks.register_all()
