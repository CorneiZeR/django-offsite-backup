"""Media sources: whole files packed into volumes, each a complete tar on its own.

A volume is streamed source -> tar -> encrypt -> disk, uploaded and deleted before the
next one starts, so media larger than the free disk works. Losing one volume loses its
files, not the night. Media is not compressed by default: images and video are already.
"""

import fnmatch
import tarfile
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from django.core import checks

from offsite_backup.backend import Backend
from offsite_backup.pipeline import feed
from offsite_backup.sealing import Sealing

__all__ = ('MEMBER_OVERHEAD', 'VOLUME_BYTES', 'VOLUME_OVERHEAD', 'MediaEntry', 'MediaSource', 'pack')

#: 4 GiB: under the 5 GB a single PUT accepts, with room for tar's own bytes
VOLUME_BYTES = 4 << 30
#: per member: a 512-byte header, padding to 512, and a PAX header for a long or non-ASCII name
MEMBER_OVERHEAD = 2048
#: per volume: the end-of-archive blocks and the stream's padding to a 10 KiB record
VOLUME_OVERHEAD = 20 * 1024


@dataclass(frozen=True)
class MediaEntry:
    """One file as the source lists it; ``name`` is its path inside the archive."""

    name: str
    size: int
    mtime: float = 0.0


def pack(entries: Iterable[MediaEntry], limit: int) -> list[list[MediaEntry]]:
    """Whole files, in order, into volumes of at most ``limit`` bytes; an oversized file goes alone."""
    budget = limit - VOLUME_OVERHEAD
    volumes: list[list[MediaEntry]] = []
    current: list[MediaEntry] = []
    filled = 0
    for entry in entries:
        cost = entry.size + MEMBER_OVERHEAD
        if current and filled + cost > budget:
            volumes.append(current)
            current, filled = [], 0
        current.append(entry)
        filled += cost
    if current:
        volumes.append(current)
    return volumes


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

    def selected(self) -> Iterator[MediaEntry]:
        """``entries()`` through the ``INCLUDE`` and ``EXCLUDE`` globs."""
        include = list(self.option('INCLUDE', []))
        exclude = list(self.option('EXCLUDE', []))
        for entry in self.entries():
            if include and not any(fnmatch.fnmatch(entry.name, pattern) for pattern in include):
                continue
            if any(fnmatch.fnmatch(entry.name, pattern) for pattern in exclude):
                continue
            yield entry

    def volumes(self) -> list[list[MediaEntry]]:
        """The selected files packed into volumes."""
        return pack(self.selected(), self.volume_bytes)

    def seal_volume(self, volume: list[MediaEntry], into: Path, sealing: Sealing) -> Path:
        """Stream one volume through tar and the sealing stages into ``into``."""
        stages = sealing.stages(compress=bool(self.option('COMPRESS', default=False)))
        with feed(stages, into=into) as stdin, tarfile.open(fileobj=stdin, mode='w|', format=tarfile.PAX_FORMAT) as tar:
            for entry in volume:
                with self.open(entry) as (stream, size):
                    member = tarfile.TarInfo(entry.name)
                    member.size = size
                    member.mtime = int(entry.mtime)
                    member.mode = 0o644
                    tar.addfile(member, stream)
        return into

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
