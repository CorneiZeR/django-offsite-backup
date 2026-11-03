"""Getting a backup back: download, decrypt, decompress, verify — and, on request, load a database.

The private key is read from a file or from the ``AGE_IDENTITY`` environment variable,
never from an argument (argv is visible in ``ps``). It is written to a 0600 file in a
private temporary directory for ``age`` and removed with it.
"""

import contextlib
import os
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from offsite_backup import naming
from offsite_backup.exceptions import BackupError, MisconfiguredError
from offsite_backup.pass_ import Components, components
from offsite_backup.pipeline import run_into

__all__ = ('IDENTITY_ENV', 'Restored', 'fetch', 'identity_file', 'keys_of', 'latest_stamp', 'restore_database')

IDENTITY_ENV = 'AGE_IDENTITY'


@dataclass(frozen=True)
class Restored:
    """One object brought back to a plain file."""

    key: str
    kind: str
    path: Path
    verified: bool


@contextlib.contextmanager
def identity_file(identity: Path | str | None, text: str | None = None) -> Iterator[str]:
    """A path ``age -i`` can read: the given file, ``text``, or ``AGE_IDENTITY``, the last two in a private file."""
    if identity and text:
        msg = 'pass an identity file or its text, not both'
        raise MisconfiguredError(msg)
    if identity:
        path = Path(identity).expanduser()
        if not path.is_file():
            msg = f'identity file {str(path)!r} not found'
            raise MisconfiguredError(msg)
        yield str(path)
        return
    secret = (text if text is not None else os.environ.get(IDENTITY_ENV, '')).strip()
    if not secret:
        msg = f'no identity: pass a file, or set {IDENTITY_ENV} to the private key'
        raise MisconfiguredError(msg)
    with tempfile.TemporaryDirectory(prefix='offsite-identity-') as directory:
        path = Path(directory) / 'identity.txt'
        path.touch(mode=0o600)
        path.write_text(secret + '\n')
        yield str(path)


def _kind(prefix: str, key: str) -> str:
    return key.removeprefix(f'{prefix}/').split('/', 1)[0]


def latest_stamp(parts: Components) -> str:
    """The stamp of the newest whole backup (config folder, else the first database's)."""
    if parts.config is not None:
        kind = naming.CONFIG
    elif parts.databases is not None:
        kind = naming.db_kind(parts.databases.targets()[0])
    else:
        kind = naming.media_kind(parts.media[0].name)
    names = [naming.parse(key) for key in parts.store.current(naming.folder(parts.settings.prefix, kind))]
    whole = sorted(name.stamp for name in names if name is not None and name.marker != naming.WITHOUT_MEDIA)
    if not whole:
        msg = f'no whole backup under {parts.settings.prefix}/{kind}/'
        raise BackupError(msg)
    return whole[-1]


def keys_of(parts: Components, stamp: str, kinds: Sequence[str] = ()) -> list[str]:
    """Every current key written at ``stamp``, directly under the prefix (not ``released/``)."""
    prefix = parts.settings.prefix
    found = []
    for key in parts.store.current(f'{prefix}/'):
        name = naming.parse(key)
        kind = _kind(prefix, key)
        if name is None or name.stamp != stamp or kind == naming.RELEASED:
            continue
        if key.count('/') != prefix.count('/') + 2:
            continue
        if kinds and kind not in kinds:
            continue
        found.append(key)
    return sorted(found)


def _plain_name(key: str) -> str:
    name = key.rsplit('/', 1)[-1]
    for suffix in ('.age', '.zst'):
        name = name.removesuffix(suffix)
    return name


#: what every zstd frame starts with; decompression follows the bytes, not the name
_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'


def _unseal(parts: Components, key: str, identity: str, into: Path) -> Path:
    """Download ``key`` next to ``into``, decrypt it, and decompress it if it is zstd."""
    sealed = into.with_name(into.name + '.sealed')
    opened = into.with_name(into.name + '.opened')
    try:
        parts.store.get(key, sealed)
        run_into([parts.sealer.unseal(identity)], into=opened, source=sealed)
        sealed.unlink()
        with opened.open('rb') as handle:
            compressed = handle.read(4) == _ZSTD_MAGIC
        if compressed:
            run_into([list(parts.settings['DECOMPRESS'])], into=into, source=opened)
        else:
            opened.replace(into)
    finally:
        sealed.unlink(missing_ok=True)
        opened.unlink(missing_ok=True)
    return into


def fetch(
    stamp: str,
    *,
    into: Path,
    identity: Path | str | None = None,
    kinds: Sequence[str] = (),
) -> list[Restored]:
    """Download and decrypt every object of ``stamp`` (``latest`` for the newest whole) into ``into``."""
    parts = components()
    if stamp == 'latest':
        stamp = latest_stamp(parts)
    keys = keys_of(parts, stamp, kinds)
    if not keys:
        msg = f'nothing stored at {stamp} under {parts.settings.prefix}/'
        raise BackupError(msg)
    restored = []
    with identity_file(identity) as secret:
        for key in keys:
            kind = _kind(parts.settings.prefix, key)
            folder = into / kind
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            plain = _unseal(parts, key, secret, folder / _plain_name(key))
            verified = False
            if kind.startswith('db') and parts.databases is not None:
                parts.databases.verify(plain)
                verified = True
            restored.append(Restored(key=key, kind=kind, path=plain, verified=verified))
    return restored


def _newest_of_kind(parts: Components, kind: str) -> str:
    """The newest current key of one kind — a deploy's dump counts: it is a whole database."""
    keys = [key for key in parts.store.current(naming.folder(parts.settings.prefix, kind)) if naming.parse(key)]
    if not keys:
        msg = f'no backup under {parts.settings.prefix}/{kind}/'
        raise BackupError(msg)
    return max(keys, key=lambda key: naming.parse(key).stamp)  # type: ignore[union-attr]


def restore_database(  # noqa: PLR0913 - every option after the alias is keyword-only
    source: str,
    alias: str,
    *,
    identity: Path | str | None = None,
    clean: bool = False,
    source_alias: str | None = None,
    identity_text: str | None = None,
) -> str:
    """Load one database backup into ``alias``; answers with the key restored.

    ``source`` is a key, a stamp, or ``latest`` (the newest dump of that database, a
    deploy's included). ``source_alias`` names whose backup it is (default: ``alias``), so
    production's ``default`` can be restored into a scratch database. The target must be
    empty unless ``clean``. ``identity_text`` is the private key itself, for a dedicated,
    narrowly used identity the server holds on purpose — never the backups' main key.
    """
    parts = components()
    if parts.databases is None:
        msg = 'DATABASES is not configured, so there is nothing to restore into.'
        raise MisconfiguredError(msg)
    kind = naming.db_kind(source_alias or alias)
    if '/' in source:
        key = source
    elif source == 'latest':
        key = _newest_of_kind(parts, kind)
    else:
        found = keys_of(parts, source, [kind])
        if not found:
            msg = f'no {kind}/ backup at {source}'
            raise BackupError(msg)
        key = found[0]
    with tempfile.TemporaryDirectory(prefix='offsite-restore-', dir=parts.settings['WORKDIR'] or None) as workspace:
        with identity_file(identity, identity_text) as secret:
            plain = _unseal(parts, key, secret, Path(workspace) / _plain_name(key))
        parts.databases.verify(plain)
        parts.databases.restore(plain, alias, clean=clean)
    return key
