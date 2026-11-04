"""Getting a backup back: download, decrypt, decompress, verify — and, on request, load a database.

The private key is read from a file or from the ``AGE_IDENTITY`` environment variable,
never from an argument (argv is visible in ``ps``). It is written to a 0600 file in a
private temporary directory for ``age`` and removed with it.

Selective: one database's dump (``kinds``), or some media files (``restore_media``) — the
volumes' manifests say which volumes hold them, so only those are downloaded, and every
file is checked against the SHA-256 its manifest recorded. Reads come from the first store
unless ``store`` names another.
"""

import contextlib
import hashlib
import os
import tarfile
import tempfile
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from offsite_backup import naming
from offsite_backup.exceptions import BackupError, MisconfiguredError
from offsite_backup.media.base import MANIFEST_SUFFIX, ManifestRow, manifest_rows
from offsite_backup.pass_ import Components, components
from offsite_backup.pipeline import run_into
from offsite_backup.stores.base import Store

__all__ = (
    'IDENTITY_ENV',
    'Restored',
    'fetch',
    'identity_file',
    'keys_of',
    'latest_stamp',
    'restore_database',
    'restore_media',
)

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


def latest_stamp(parts: Components, store: Store | None = None) -> str:
    """The stamp of the newest whole backup (config folder, else the first database's)."""
    if parts.config is not None:
        kind = naming.CONFIG
    elif parts.databases is not None:
        kind = naming.db_kind(parts.databases.targets()[0])
    else:
        kind = naming.media_kind(parts.media[0].name)
    store = store or parts.store
    names = [naming.parse(key) for key in store.current(naming.folder(parts.settings.prefix, kind))]
    whole = sorted(name.stamp for name in names if name is not None and name.marker != naming.WITHOUT_MEDIA)
    if not whole:
        msg = f'no whole backup under {parts.settings.prefix}/{kind}/'
        raise BackupError(msg)
    return whole[-1]


def keys_of(parts: Components, stamp: str, kinds: Sequence[str] = (), store: Store | None = None) -> list[str]:
    """Every current key written at ``stamp``, directly under the prefix (not ``released/``)."""
    prefix = parts.settings.prefix
    found = []
    for key in (store or parts.store).current(f'{prefix}/'):
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


def _unseal(parts: Components, key: str, identity: str, into: Path, store: Store | None = None) -> Path:
    """Download ``key`` next to ``into``, decrypt it, and decompress it if it is zstd."""
    sealed = into.with_name(into.name + '.sealed')
    opened = into.with_name(into.name + '.opened')
    try:
        (store or parts.store).get(key, sealed)
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
    store: str | None = None,
) -> list[Restored]:
    """Download and decrypt every object of ``stamp`` (``latest`` for the newest whole) into ``into``."""
    parts = components()
    source = parts.store_named(store)
    if stamp == 'latest':
        stamp = latest_stamp(parts, source)
    keys = keys_of(parts, stamp, kinds, source)
    if not keys:
        msg = f'nothing stored at {stamp} under {parts.settings.prefix}/'
        raise BackupError(msg)
    restored = []
    with identity_file(identity) as secret:
        for key in keys:
            kind = _kind(parts.settings.prefix, key)
            folder = into / kind
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            plain = _unseal(parts, key, secret, folder / _plain_name(key), source)
            verified = False
            if kind.startswith('db') and parts.databases is not None:
                parts.databases.verify(plain)
                verified = True
            restored.append(Restored(key=key, kind=kind, path=plain, verified=verified))
    return restored


def _matches(name: str, paths: Sequence[str]) -> bool:
    """``name`` is one of ``paths``, or inside one of them taken as a folder."""
    return any(name == path or name.startswith(path + '/') for path in paths)


def _inside(root: Path, name: str) -> Path:
    target = (root / name).resolve()
    if not target.is_relative_to(root.resolve()):
        msg = f'{name!r} leaves the restore directory'
        raise BackupError(msg)
    return target


def _extract(volume: Path, root: Path, paths: Sequence[str], expected: dict[str, ManifestRow] | None) -> list[Path]:
    """The matching files of one decrypted volume under ``root``, each checked against its manifest row."""
    found = []
    with tarfile.open(volume) as tar:
        for member in tar:
            if not member.isreg() or not _matches(member.name, paths):
                continue
            target = _inside(root, member.name)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = hashlib.sha256()
            with tar.extractfile(member) as stream, target.open('wb') as sink:  # type: ignore[union-attr]
                for block in iter(lambda: stream.read(1 << 20), b''):
                    digest.update(block)
                    sink.write(block)
            row = (expected or {}).get(member.name)
            if row is not None and row.sha256 != digest.hexdigest():
                msg = f'{member.name}: its SHA-256 does not match the manifest — the volume is not what was written'
                raise BackupError(msg)
            found.append(target)
    missing = sorted(set(expected or {}) - {path.relative_to(root.resolve()).as_posix() for path in found})
    if missing:
        msg = f'{volume.name} lacks what its manifest lists: {", ".join(missing[:5])}'
        raise BackupError(msg)
    return found


def _media_volumes(parts: Components, keys: list[str]) -> dict[str, dict[int, dict[str, str]]]:
    """``{kind: {number: {'volume': key, 'manifest': key}}}`` for the media keys among ``keys``."""
    volumes: dict[str, dict[int, dict[str, str]]] = {}
    for key in keys:
        kind, name = _kind(parts.settings.prefix, key), naming.parse(key)
        if not kind.startswith('media') or name is None or name.number is None:
            continue
        role = 'manifest' if name.suffix == MANIFEST_SUFFIX else 'volume'
        volumes.setdefault(kind, {}).setdefault(name.number, {})[role] = key
    return volumes


def restore_media(  # noqa: PLR0913 - every option after the paths is keyword-only
    stamp: str,
    paths: Sequence[str],
    *,
    into: Path,
    identity: Path | str | None = None,
    kinds: Sequence[str] = (),
    store: str | None = None,
) -> list[Restored]:
    """Bring back the media files at ``paths`` (a file, or a folder for everything under it).

    Only the volumes whose manifest lists a match are downloaded, and each file is checked
    against its manifest (``verified``); a volume written before manifests existed is read
    whole and its files are not verified. Files land under ``into/<kind>/<path>``.
    """
    wanted = [path.strip().strip('/') for path in paths if path.strip().strip('/')]
    if not wanted:
        msg = 'name at least one media path to restore'
        raise BackupError(msg)
    parts = components()
    source = parts.store_named(store)
    if stamp == 'latest':
        stamp = latest_stamp(parts, source)
    volumes = _media_volumes(parts, keys_of(parts, stamp, kinds, source))
    restored: list[Restored] = []
    with (
        identity_file(identity) as secret,
        tempfile.TemporaryDirectory(prefix='offsite-restore-', dir=parts.settings['WORKDIR'] or None) as workspace,
    ):
        here = Path(workspace)
        for kind, numbered in sorted(volumes.items()):
            root = into / kind
            for _, held in sorted(numbered.items()):
                if 'volume' not in held:
                    continue
                expected = None
                if 'manifest' in held:
                    rows = manifest_rows(_unseal(parts, held['manifest'], secret, here / 'manifest.json', source))
                    expected = {row.path: row for row in rows if _matches(row.path, wanted)}
                    if not expected:
                        continue
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                plain = _unseal(parts, held['volume'], secret, here / 'volume.tar', source)
                restored += [
                    Restored(key=held['volume'], kind=kind, path=path, verified=expected is not None)
                    for path in _extract(plain, root, wanted, expected)
                ]
                plain.unlink()
    if not restored:
        msg = f'no media file at {stamp} matches {", ".join(wanted)}'
        raise BackupError(msg)
    return restored


def _newest_of_kind(parts: Components, kind: str, store: Store) -> str:
    """The newest current key of one kind — a deploy's dump counts: it is a whole database."""
    keys = [key for key in store.current(naming.folder(parts.settings.prefix, kind)) if naming.parse(key)]
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
    store: str | None = None,
) -> str:
    """Load one database backup into ``alias``; answers with the key restored.

    ``source`` is a key, a stamp, or ``latest`` (the newest dump of that database, a
    deploy's included). ``source_alias`` names whose backup it is (default: ``alias``), so
    production's ``default`` can be restored into a scratch database. The target must be
    empty unless ``clean``. ``identity_text`` is the private key itself, for a dedicated,
    narrowly used identity the server holds on purpose — never the backups' main key.
    ``store`` reads from the store of that ``NAME`` instead of the first.
    """
    parts = components()
    if parts.databases is None:
        msg = 'DATABASES is not configured, so there is nothing to restore into.'
        raise MisconfiguredError(msg)
    held = parts.store_named(store)
    kind = naming.db_kind(source_alias or alias)
    if '/' in source:
        key = source
    elif source == 'latest':
        key = _newest_of_kind(parts, kind, held)
    else:
        found = keys_of(parts, source, [kind], held)
        if not found:
            msg = f'no {kind}/ backup at {source}'
            raise BackupError(msg)
        key = found[0]
    with tempfile.TemporaryDirectory(prefix='offsite-restore-', dir=parts.settings['WORKDIR'] or None) as workspace:
        with identity_file(identity, identity_text) as secret:
            plain = _unseal(parts, key, secret, Path(workspace) / _plain_name(key), held)
        parts.databases.verify(plain)
        parts.databases.restore(plain, alias, clean=clean)
    return key
