"""What a backend's ``check()`` answers, without Django.

The same backends run under Django's checks framework and under the plain command line, so a
finding is the package's own record, shaped like Django's check message (``msg``, ``hint``,
``id``, ``level``). ``offsite_backup.checks`` hands each one to Django as a check message; the
command line prints them.
"""

from dataclasses import dataclass

__all__ = ('ERROR', 'WARNING', 'CheckMessage', 'Error', 'Warning')

#: Django's numeric levels, so a finding converts to a check message without a table
WARNING = 30
ERROR = 40


@dataclass(frozen=True, init=False)
class CheckMessage:
    """One finding: what is wrong, how to fix it, and its stable code."""

    level: int
    msg: str
    hint: str | None
    id: str | None

    def __init__(self, level: int, msg: str, hint: str | None = None, id: str | None = None) -> None:  # noqa: A002 - Django's name
        """The same arguments as Django's ``CheckMessage``, less ``obj``."""
        object.__setattr__(self, 'level', level)
        object.__setattr__(self, 'msg', msg)
        object.__setattr__(self, 'hint', hint)
        object.__setattr__(self, 'id', id)

    def is_serious(self, level: int = ERROR) -> bool:
        """At or above ``level`` — an error, by default."""
        return self.level >= level

    def __str__(self) -> str:
        """``offsite_backup.E001: ...`` and the hint on its own line, as ``manage.py check`` prints it."""
        text = f'{self.id}: {self.msg}' if self.id else self.msg
        return f'{text}\n\tHINT: {self.hint}' if self.hint else text


class Error(CheckMessage):
    """A finding that stops a pass, or would make it protect nothing."""

    def __init__(self, msg: str, hint: str | None = None, id: str | None = None) -> None:  # noqa: A002 - Django's name
        """``Error(msg, hint=..., id=...)``, as Django's."""
        super().__init__(ERROR, msg, hint, id)


class Warning(CheckMessage):  # noqa: A001 - Django's name, so a backend reads the same either way
    """A finding worth knowing that does not stop a pass."""

    def __init__(self, msg: str, hint: str | None = None, id: str | None = None) -> None:  # noqa: A002 - Django's name
        """``Warning(msg, hint=..., id=...)``, as Django's."""
        super().__init__(WARNING, msg, hint, id)
