"""`age <https://age-encryption.org>`_ without the binary, through `pyrage <https://pypi.org/project/pyrage/>`_.

The same format as ``age``: a file sealed here opens with ``age -d``, and one sealed by
``age`` opens here. A pipeline stage is a process, so the stage is this interpreter running
this module (``python -m offsite_backup.encryption.pyrage seal|open``), streaming stdin to
stdout through ``pyrage.encrypt_io`` / ``decrypt_io`` — memory stays flat whatever the size.
pyrage is imported in that process and in ``check()``, never when this module is imported.
"""

import sys
from collections.abc import Sequence
from typing import Any

from offsite_backup import findings as checks
from offsite_backup.encryption.age_cli import AgeCli

__all__ = ('Pyrage', 'main')

_MODULE = 'offsite_backup.encryption.pyrage'


def _recipient(text: str) -> Any:
    import pyrage  # noqa: PLC0415 - the [pyrage] extra

    if text.startswith('age1'):
        return pyrage.x25519.Recipient.from_str(text)
    return pyrage.ssh.Recipient.from_str(text)


def _identities(text: str) -> list[Any]:
    """Every identity in an identity file: ``AGE-SECRET-KEY-1...`` lines, or one SSH private key."""
    import pyrage  # noqa: PLC0415 - the [pyrage] extra

    if '-----BEGIN' in text:
        return [pyrage.ssh.Identity.from_buffer(text.encode())]
    return [
        pyrage.x25519.Identity.from_str(line.strip())
        for line in text.splitlines()
        if line.strip().upper().startswith('AGE-SECRET-KEY-')
    ]


class Pyrage(AgeCli):
    """``RECIPIENTS`` as for ``AgeCli``; ``PYTHON`` is the interpreter a stage runs (default: this one)."""

    @property
    def python(self) -> str:
        """The interpreter of each stage: a server like uWSGI is ``sys.executable`` and is not Python."""
        return str(self.option('PYTHON') or sys.executable)

    def binaries(self) -> tuple[str, ...]:
        """None: the interpreter is the only program, and it is running."""
        return ()

    def pipeline(self, extra_recipients: Sequence[str] = ()) -> list[str]:
        """``python -m ...pyrage seal A B``: any one of the private halves opens the result."""
        return [self.python, '-m', _MODULE, 'seal', *self.recipients, *extra_recipients]

    def unseal(self, identity: str) -> list[str]:
        """``python -m ...pyrage open <identity file>``."""
        return [self.python, '-m', _MODULE, 'open', identity]

    def check(self) -> list[checks.CheckMessage]:
        """E008 as for ``age``, and each recipient one pyrage itself accepts."""
        found = super().check()
        if any(message.id == 'offsite_backup.E008' for message in found):
            return found
        try:
            import pyrage  # noqa: F401, PLC0415 - absent: E002 already names the extra
        except ImportError:
            return found
        for recipient in self.recipients:
            try:
                _recipient(recipient)
            except Exception:  # noqa: BLE001, PERF203 - pyrage's refusal is the finding
                found.append(
                    checks.Error(
                        f"{self.label}['RECIPIENTS'] has {recipient[:12]!r}..., which pyrage cannot read.",
                        hint='An age recipient (age1...) or an SSH public key (ssh-ed25519, ssh-rsa).',
                        id='offsite_backup.E008',
                    )
                )
        return found


def main(argv: Sequence[str] | None = None) -> int:
    """``seal RECIPIENT...`` or ``open IDENTITY_FILE``, stdin to stdout; 1 and one line of stderr on failure."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        import pyrage  # noqa: PLC0415 - the [pyrage] extra

        if len(args) >= 2 and args[0] == 'seal':  # noqa: PLR2004 - a verb and at least one recipient
            pyrage.encrypt_io(sys.stdin.buffer, sys.stdout.buffer, [_recipient(text) for text in args[1:]])
        elif len(args) == 2 and args[0] == 'open':  # noqa: PLR2004 - a verb and a file
            with open(args[1]) as handle:  # noqa: PTH123 - one read of a path from argv
                identities = _identities(handle.read())
            if not identities:
                sys.stderr.write('pyrage: the identity file holds no age or SSH identity\n')
                return 1
            pyrage.decrypt_io(sys.stdin.buffer, sys.stdout.buffer, identities)
        else:
            sys.stderr.write('usage: python -m offsite_backup.encryption.pyrage seal RECIPIENT... | open IDENTITY\n')
            return 2
        sys.stdout.flush()
    except Exception as error:  # noqa: BLE001 - the class and pyrage's text, never a key: they reach a chat
        sys.stderr.write(f'pyrage: {type(error).__name__}: {error}\n')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
