"""Test doubles that need no database server: a database source that dumps a fixed text."""

from pathlib import Path

from offsite_backup.databases.base import DatabaseSource
from offsite_backup.exceptions import BackupError
from offsite_backup.notifiers.base import Notifier
from offsite_backup.outcome import Outcome
from offsite_backup.pipeline import run_into
from offsite_backup.sealing import Sealing

DUMPED: list[str] = []
RESTORED: list[tuple[str, str, bool]] = []
#: what each alias holds as far as the drill can count: restored or cleared databases
HELD: dict[str, dict[str, int]] = {}
CLEARED: list[str] = []


class FakeDatabase(DatabaseSource):
    """Dumps ``-- dump of <alias>`` and a line per table of ``LIVE[alias]`` through the real pipeline.

    Aliases in ``FAIL`` exit 1 mid-stream; aliases in ``FAIL_RESTORE`` refuse to load.
    """

    def targets(self) -> list[str]:
        return list(self.option('ALIASES', ['default']))

    def suffix(self) -> str:
        return 'sql.zst.age'

    def live(self, alias: str) -> dict[str, int]:
        return dict(self.option('LIVE', {}).get(alias, {}))

    def dump(self, alias: str, into: Path, sealing: Sealing) -> Path:
        DUMPED.append(alias)
        if alias in self.option('FAIL', []):
            command = ['sh', '-c', 'printf partial; exit 1']
        else:
            tables = [f'-- table {name} {rows}' for name, rows in self.live(alias).items()]
            command = ['printf', '%s\\n', f'-- dump of {alias}', *tables, '-- PostgreSQL database dump complete']
        return run_into([command, *sealing.stages()], into=into)

    def verify(self, plain: Path) -> None:
        if b'dump complete' not in plain.read_bytes():
            msg = 'cut short'
            raise BackupError(msg)

    def restore(self, plain: Path, alias: str, *, clean: bool = False) -> None:
        text = plain.read_text()
        RESTORED.append((text, alias, clean))
        source = text.split('\n', 1)[0].removeprefix('-- dump of ')
        if source in self.option('FAIL_RESTORE', []):
            msg = f'cannot load {source}'
            raise BackupError(msg)
        lines = [line.split() for line in text.splitlines() if line.startswith('-- table ')]
        HELD[alias] = {parts[2]: int(parts[3]) for parts in lines}

    def tables(self, alias: str) -> dict[str, int]:
        if alias in self.option('UNCOUNTABLE', []):
            msg = f'cannot count {alias}'
            raise BackupError(msg)
        return dict(HELD[alias]) if alias in HELD else self.live(alias)

    def clear(self, alias: str) -> None:
        CLEARED.append(alias)
        HELD[alias] = {}


class Recorder(Notifier):
    """A notifier that cannot reach the network, so it runs under ``silence()``."""

    REACHES_NETWORK = False
    sent: list[tuple[str, str]] = []

    def send(self, outcome: Outcome, text: str) -> bool:
        Recorder.sent.append((outcome.kind, text))
        return True


class Exploding(Notifier):
    """A notifier whose outage must not cost the backup."""

    REACHES_NETWORK = False

    def send(self, outcome: Outcome, text: str) -> bool:
        raise RuntimeError('chat is down')


class Network(Notifier):
    """Stands in for a real chat notifier; must never be called under ``silence()``."""

    called: list[str] = []

    def send(self, outcome: Outcome, text: str) -> bool:
        Network.called.append(text)
        return True


class FakeBot:
    """Records which django-aiogram method a notifier used."""

    calls: list[tuple[str, str, dict]] = []

    def send_raw(self, function, **kwargs):
        FakeBot.calls.append(('send_raw', function, kwargs))

    def send(self, function, **kwargs):
        FakeBot.calls.append(('send', function, kwargs))


def fake_bot():
    return FakeBot()


NOT_CALLABLE = 42
