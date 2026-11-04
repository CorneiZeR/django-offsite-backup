"""Media through a Django storage backend: ``STORAGES[alias]`` — django-storages' S3, GCS, Azure, any other.

Files are listed with ``listdir`` from ``PREFIX`` down and streamed into the tar a block at
a time; nothing reads a whole file. A django-storages S3 file is read from its object's body
directly (``file.obj.get()['Body']``): its own ``read()`` would first download the whole
object into a ``SpooledTemporaryFile``, kept in memory unless ``AWS_S3_MAX_MEMORY_SIZE`` is
set. Other cloud backends spool the same way before handing a file over — set their own
max-memory setting, or read an S3 bucket with ``S3Source``, which also lists in one pass.
"""

import contextlib
import logging
import posixpath
from collections.abc import Iterator
from typing import IO, Any

from django.core import checks

from offsite_backup.media.base import MediaEntry, MediaSource

__all__ = ('StorageSource',)

logger = logging.getLogger('offsite_backup')


class StorageSource(MediaSource):
    """``STORAGE`` (an alias of ``STORAGES``, default ``default``), ``PREFIX`` (a folder in it, default all)."""

    REACHES_NETWORK = True

    @property
    def alias(self) -> str:
        """The storage alias."""
        return str(self.option('STORAGE') or 'default')

    @property
    def storage(self) -> Any:
        """The storage, as the project configured it."""
        from django.core.files.storage import storages  # noqa: PLC0415 - read when the source is used

        return storages[self.alias]

    @property
    def prefix(self) -> str:
        """The folder listed, without surrounding slashes."""
        return str(self.option('PREFIX', '')).strip().strip('/')

    def _walk(self, storage: Any, folder: str) -> Iterator[str]:
        directories, files = storage.listdir(folder)
        for name in sorted(files):
            if name:
                yield posixpath.join(folder, name) if folder else name
        for name in sorted(directories):
            if name and name not in {'.', '..'}:
                yield from self._walk(storage, posixpath.join(folder, name) if folder else name)

    def entries(self) -> Iterator[MediaEntry]:
        """Every file under ``PREFIX``, files before folders at each level; a file that vanished is left out."""
        storage = self.storage
        for name in self._walk(storage, self.prefix):
            try:
                size = int(storage.size(name))
            except (FileNotFoundError, OSError) as error:  # deleted between listing and asking
                logger.warning('Media file not listed', extra={'ob_path': name, 'ob_error': str(error)})
                continue
            try:
                mtime = storage.get_modified_time(name).timestamp()
            except (NotImplementedError, OSError, AttributeError):
                mtime = 0.0
            yield MediaEntry(name, size, mtime)

    @contextlib.contextmanager
    def open(self, entry: MediaEntry) -> Iterator[tuple[IO[bytes], int]]:
        """The file as a stream and its size now: an S3 object's body, or the storage's own file."""
        with self.storage.open(entry.name, 'rb') as handle:
            body = _s3_body(handle)
            if body is not None:
                stream, size = body
                try:
                    yield stream, size
                finally:
                    stream.close()
                return
            yield handle, int(getattr(handle, 'size', None) or entry.size)

    def check(self) -> list[checks.CheckMessage]:
        """``STORAGE`` names a storage in ``STORAGES``."""
        from django.conf import settings  # noqa: PLC0415

        found = super().check()
        if self.alias not in (getattr(settings, 'STORAGES', None) or {}):
            found.append(
                checks.Error(
                    f"{self.label}['STORAGE'] is {self.alias!r}, which STORAGES does not define.",
                    id='offsite_backup.E001',
                )
            )
        return found

    def deploy_check(self) -> list[checks.CheckMessage]:
        """E011: ``PREFIX`` cannot be listed from here (credentials, network, a missing bucket)."""
        found = super().deploy_check()
        try:
            self.storage.listdir(self.prefix)
        except Exception as error:  # noqa: BLE001 - any failure here is the finding
            found.append(
                checks.Error(
                    f'{self.label}: storage {self.alias!r} cannot be listed at {self.prefix or "its root"!r} '
                    f'({type(error).__name__}).',
                    hint="Check the storage's credentials and location in the container that runs the backup.",
                    id='offsite_backup.E011',
                )
            )
        return found


def _s3_body(handle: Any) -> tuple[IO[bytes], int] | None:
    """A django-storages ``S3File``'s object body and length, streamed; ``None`` for any other file."""
    obj = getattr(handle, 'obj', None)
    if obj is None or not callable(getattr(obj, 'get', None)) or getattr(handle, '_file', None) is not None:
        return None
    found = obj.get()
    return found['Body'], int(found['ContentLength'])
