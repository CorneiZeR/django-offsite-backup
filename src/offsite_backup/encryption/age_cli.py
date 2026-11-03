"""`age <https://age-encryption.org>`_ through its command-line tool."""

from collections.abc import Sequence

from django.core import checks

from offsite_backup.encryption.base import Sealer

__all__ = ('AgeCli',)

_PUBLIC = ('age1', 'ssh-ed25519 ', 'ssh-rsa ')


class AgeCli(Sealer):
    """``age -r <recipient> [-r ...]``; ``RECIPIENTS`` holds public keys only."""

    REQUIRED = ('RECIPIENTS',)
    BINARIES = ('age',)

    @property
    def recipients(self) -> list[str]:
        """``RECIPIENTS`` as a list; a single string is accepted."""
        value = self.option('RECIPIENTS', [])
        values = [value] if isinstance(value, str) else list(value)
        return [str(item).strip() for item in values if str(item).strip()]

    def pipeline(self, extra_recipients: Sequence[str] = ()) -> list[str]:
        """``age -r A -r B``: any one of the private halves opens the result."""
        argv = ['age']
        for recipient in [*self.recipients, *extra_recipients]:
            argv += ['-r', recipient]
        return argv

    def unseal(self, identity: str) -> list[str]:
        """``age -d -i <file>``."""
        return ['age', '-d', '-i', identity]

    def check(self) -> list[checks.CheckMessage]:
        """Every recipient looks like a public key — a private one here would defeat the design."""
        found = super().check()
        for recipient in self.recipients:
            if recipient.upper().startswith('AGE-SECRET-KEY-'):
                found.append(
                    checks.Error(
                        f"{self.label}['RECIPIENTS'] holds a PRIVATE age key.",
                        hint='Put the public half (age1...) here; the private half belongs off this machine.',
                        id='offsite_backup.E008',
                    )
                )
            elif not recipient.startswith(_PUBLIC):
                found.append(
                    checks.Error(
                        f"{self.label}['RECIPIENTS'] has {recipient[:12]!r}..., which is not an age recipient.",
                        hint='A recipient starts with age1, ssh-ed25519 or ssh-rsa.',
                        id='offsite_backup.E008',
                    )
                )
        return found
