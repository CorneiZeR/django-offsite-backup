"""Sealers: what turns a plain stream into one only the off-machine key can read."""

from collections.abc import Sequence

from offsite_backup.backend import Backend

__all__ = ('Sealer',)


class Sealer(Backend):
    """Encrypts to public keys. The machine that writes a backup cannot read one."""

    def pipeline(self, extra_recipients: Sequence[str] = ()) -> list[str]:
        """The argv of a stage that reads plain bytes on stdin and writes sealed bytes."""
        raise NotImplementedError

    def unseal(self, identity: str) -> list[str]:
        """The argv of a stage that decrypts stdin with the private key file ``identity``."""
        raise NotImplementedError
