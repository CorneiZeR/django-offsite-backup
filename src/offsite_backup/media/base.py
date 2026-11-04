"""Media sources: whole files packed into volumes, each a complete tar on its own.

A volume is streamed source -> tar -> encrypt -> disk, uploaded and deleted before the
next one starts, so media larger than the free disk works. Losing one volume loses its
files, not the night. Media is not compressed by default: images and video are already.

Every volume has a **manifest** beside it — each file's path, size and SHA-256, computed
while the file streams into the tar — sealed like the volume, so a restore of one file or
one folder downloads the manifests and only the volumes that hold it.
"""

import contextlib
import fnmatch
import hashlib
import json
import tarfile
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import IO, Any

from offsite_backup import findings as checks
from offsite_backup.backend import Backend
from offsite_backup.exceptions import SourceMissingError
from offsite_backup.pipeline import feed
from offsite_backup.sealing import Sealing

__all__ = (
    'MANIFEST_FORMAT',
    'MANIFEST_SUFFIX',
    'MEMBER_OVERHEAD',
    'VOLUME_BYTES',
    'VOLUME_OVERHEAD',
    'Item',
    'ManifestRow',
    'MediaEntry',
    'MediaSource',
    'Packer',
    'Ship',
    'manifest_rows',
    'pack',
    'write_manifest',
)

#: 4 GiB: under the 5 GB a single PUT accepts, with room for tar's own bytes
VOLUME_BYTES = 4 << 30
#: per member: a 512-byte header, padding to 512, and a PAX header for a long or non-ASCII name
MEMBER_OVERHEAD = 2048
#: per volume: the end-of-archive blocks and the stream's padding to a 10 KiB record
VOLUME_OVERHEAD = 20 * 1024
#: ``<stamp>.<NNN>.manifest.json.age`` sits beside ``<stamp>.<NNN>.tar.age``
MANIFEST_SUFFIX = 'manifest.json.age'
MANIFEST_FORMAT = 1


@dataclass(frozen=True)
class MediaEntry:
    """One file as the source lists it; ``name`` is its path inside the archive."""

    name: str
    size: int
    mtime: float = 0.0


@dataclass(frozen=True)
class ManifestRow:
    """One file as it went into a volume: the bytes written, not the size listed."""

    path: str
    size: int
    sha256: str


#: a file and how to open it: ``(stream, size now)``
Item = tuple[MediaEntry, Callable[[], AbstractContextManager[tuple[IO[bytes], int]]]]
#: what a pass does with each sealed volume and the rows it holds, before the next one opens
Ship = Callable[[Path, list[ManifestRow]], None]


class Packer:
    """The volume budget: whole files, an oversized one alone, tar's own bytes counted."""

    def __init__(self, limit: int) -> None:
        """A budget of ``limit`` bytes per volume."""
        self.budget = limit - VOLUME_OVERHEAD
        self.filled: int | None = None

    def place(self, entry: MediaEntry) -> bool:
        """Count ``entry`` in; whether it opens a new volume."""
        cost = entry.size + MEMBER_OVERHEAD
        new = self.filled is None or self.filled + cost > self.budget
        self.filled = (0 if new or self.filled is None else self.filled) + cost
        return new


def pack(entries: Iterable[MediaEntry], limit: int) -> list[list[MediaEntry]]:
    """Whole files, in order, into volumes of at most ``limit`` bytes; an oversized file goes alone."""
    packer = Packer(limit)
    volumes: list[list[MediaEntry]] = []
    for entry in entries:
        if packer.place(entry):
            volumes.append([])
        volumes[-1].append(entry)
    return volumes


class _Hashing:
    """A read-only stream that hashes what ``tarfile`` copies out of it."""

    def __init__(self, stream: IO[bytes]) -> None:
        self.stream = stream
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        data = self.stream.read(size)
        self.digest.update(data)
        return data


class _Volume:
    """One volume being written: a tar streamed into the sealing stages, a row per file."""

    def __init__(self, into: Path, stages: list[list[str]]) -> None:
        self.into = into
        self.rows: list[ManifestRow] = []
        self._stack = contextlib.ExitStack()
        try:
            stdin = self._stack.enter_context(feed(stages, into=into))
            tar = tarfile.open(fileobj=stdin, mode='w|', format=tarfile.PAX_FORMAT)  # noqa: SIM115 - the stack closes it
            self._tar = self._stack.enter_context(tar)
        except BaseException:
            self._stack.close()
            raise

    def add(self, entry: MediaEntry, stream: IO[bytes], size: int) -> None:
        member = tarfile.TarInfo(entry.name)
        member.size = size
        member.mtime = int(entry.mtime)
        member.mode = 0o644
        hashing = _Hashing(stream)
        self._tar.addfile(member, hashing)
        self.rows.append(ManifestRow(entry.name, size, hashing.digest.hexdigest()))

    def close(self) -> None:
        self._stack.close()

    def abort(self, error: BaseException) -> None:
        # the pipeline is killed, or raises the stage's own error, which says more
        self._stack.__exit__(type(error), error, error.__traceback__)


def write_manifest(rows: list[ManifestRow], into: Path, sealing: Sealing, *, volume: str) -> Path:
    """The rows of one volume as JSON, sealed (not compressed) into ``into``."""
    document = {'format': MANIFEST_FORMAT, 'volume': volume, 'files': [asdict(row) for row in rows]}
    with feed(sealing.stages(compress=False), into=into) as stdin:
        stdin.write(json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode())
    return into


def manifest_rows(plain: Path) -> list[ManifestRow]:
    """The rows of a decrypted manifest."""
    document: dict[str, Any] = json.loads(plain.read_text())
    return [ManifestRow(str(row['path']), int(row['size']), str(row['sha256'])) for row in document['files']]


class MediaSource(Backend):
    """``NAME``, ``VOLUME_BYTES``, ``INCLUDE``/``EXCLUDE`` globs, ``COMPRESS`` (off)."""

    @property
    def name(self) -> str:
        """The kind suffix: ``media/`` for an unnamed source, ``media-<name>/`` otherwise."""
        return str(self.option('NAME', ''))

    @property
    def volume_bytes(self) -> int:
        """The most one volume may hold."""
        return int(self.option('VOLUME_BYTES', VOLUME_BYTES))

    def suffix(self) -> str:
        """``tar.age``, or ``tar.zst.age`` with ``COMPRESS`` on."""
        return 'tar.zst.age' if self.option('COMPRESS', default=False) else 'tar.age'

    def entries(self) -> Iterable[MediaEntry]:
        """Every file the source holds, in a stable order."""
        raise NotImplementedError

    def open(self, entry: MediaEntry) -> AbstractContextManager[tuple[IO[bytes], int]]:
        """A readable stream of the file and its size at the moment of opening."""
        raise NotImplementedError

    def wanted(self, name: str) -> bool:
        """Whether a file's archive name passes the ``INCLUDE`` and ``EXCLUDE`` globs."""
        include = self.strings('INCLUDE')
        if include and not any(fnmatch.fnmatch(name, pattern) for pattern in include):
            return False
        return not any(fnmatch.fnmatch(name, pattern) for pattern in self.strings('EXCLUDE'))

    def selected(self) -> Iterator[MediaEntry]:
        """``entries()`` through the ``INCLUDE`` and ``EXCLUDE`` globs."""
        return (entry for entry in self.entries() if self.wanted(entry.name))

    def items(self) -> Generator[Item, None, None]:
        """Each selected file with its opener; a source that can only stream overrides this alone."""
        for entry in self.selected():
            yield entry, lambda entry=entry: self.open(entry)  # type: ignore[misc]

    def _nothing_found(self) -> None:
        if self.required:
            msg = f'{self.label}: no media files found, and the source is required'
            raise SourceMissingError(msg)

    def volumes(self) -> list[list[MediaEntry]]:
        """The selected files packed into volumes; none at all fails a required source."""
        volumes = pack(self.selected(), self.volume_bytes)
        if not volumes:
            self._nothing_found()
        return volumes

    def _stages(self, sealing: Sealing) -> list[list[str]]:
        return sealing.stages(compress=bool(self.option('COMPRESS', default=False)))

    def seal_volume(self, volume: list[MediaEntry], into: Path, sealing: Sealing) -> Path:
        """Stream one volume through tar and the sealing stages into ``into``."""
        writer = _Volume(into, self._stages(sealing))
        try:
            for entry in volume:
                with self.open(entry) as (stream, size):
                    writer.add(entry, stream, size)
        except BaseException as error:
            writer.abort(error)
            raise
        writer.close()
        return into

    def seal_volumes(self, path_for: Callable[[int], Path], sealing: Sealing, ship: Ship) -> int:
        """Pack while streaming: each volume sealed, then handed to ``ship`` before the next one opens.

        Answers how many volumes there were; none at all fails a required source.
        """
        packer = Packer(self.volume_bytes)
        writer: _Volume | None = None
        count = 0
        try:
            with contextlib.closing(self.items()) as items:
                for entry, opener in items:
                    if packer.place(entry):
                        if writer is not None:
                            done, writer = writer, None
                            done.close()
                            ship(done.into, done.rows)
                        count += 1
                        writer = _Volume(path_for(count), self._stages(sealing))
                    with opener() as (stream, size):
                        writer.add(entry, stream, size)  # type: ignore[union-attr]
        except BaseException as error:
            if writer is not None:
                writer.abort(error)
            raise
        if writer is not None:
            writer.close()
            ship(writer.into, writer.rows)
        if not count:
            self._nothing_found()
        return count

    def check(self) -> list[checks.CheckMessage]:
        """``VOLUME_BYTES`` is a positive size below what one PUT takes."""
        found = super().check()
        size = self.option('VOLUME_BYTES', VOLUME_BYTES)
        if not isinstance(size, int) or isinstance(size, bool) or not VOLUME_OVERHEAD < size <= 5 * 10**9:
            found.append(
                checks.Error(
                    f"{self.label}['VOLUME_BYTES'] is {size!r}; "
                    f'expected more than {VOLUME_OVERHEAD} bytes and at most 5 GB.',
                    hint='One S3 PUT stops at 5 GB; the default is 4 GiB.',
                    id='offsite_backup.E001',
                )
            )
        return found
