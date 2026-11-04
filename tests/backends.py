"""Test doubles that need no database server: a database source that dumps a fixed text."""

from pathlib import Path

from offsite_backup.databases.base import DatabaseSource
from offsite_backup.exceptions import BackupError
from offsite_backup.notifiers.base import Notifier
from offsite_backup.outcome import Outcome
from offsite_backup.pipeline import run_into
from offsite_backup.sealing import Sealing
from offsite_backup.stores.filesystem import DirectoryStore

DUMPED: list[str] = []
RESTORED: list[tuple[str, str, bool]] = []


class FakeDatabase(DatabaseSource):
    """Dumps ``-- dump of <alias>`` through the real pipeline; aliases in ``FAIL`` exit 1 mid-stream."""

    def targets(self) -> list[str]:
        return list(self.option('ALIASES', ['default']))

    def suffix(self) -> str:
        return 'sql.zst.age'

    def dump(self, alias: str, into: Path, sealing: Sealing) -> Path:
        DUMPED.append(alias)
        if alias in self.option('FAIL', []):
            command = ['sh', '-c', 'printf partial; exit 1']
        else:
            command = ['printf', '%s\\n', f'-- dump of {alias}', '-- PostgreSQL database dump complete']
        return run_into([command, *sealing.stages()], into=into)

    def verify(self, plain: Path) -> None:
        if b'dump complete' not in plain.read_bytes():
            msg = 'cut short'
            raise BackupError(msg)

    def restore(self, plain: Path, alias: str, *, clean: bool = False) -> None:
        RESTORED.append((plain.read_text(), alias, clean))


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


class RefusingStore(DirectoryStore):
    """A directory store whose disk is full: every upload is refused."""

    def put(self, path: Path, key: str, *, now=None):
        raise OSError(28, 'No space left on device')
