"""Media in a directory: ``MEDIA_ROOT``, any path, or a Docker volume mounted into the container."""

import contextlib
import logging
import os
from collections.abc import Iterator
from pathlib import Path
from typing import IO

from django.core import checks

from offsite_backup.exceptions import MisconfiguredError, SourceMissingError
from offsite_backup.media.base import MediaEntry, MediaSource

__all__ = ('DirectorySource',)

logger = logging.getLogger('offsite_backup')


class DirectorySource(MediaSource):
    """``ROOT`` (default ``settings.MEDIA_ROOT``); symlinks are not followed."""

    @property
    def root(self) -> Path:
        """The directory archived; names inside the tar are relative to it."""
        from django.conf import settings  # noqa: PLC0415 - read when the source is used

        root = str(self.option('ROOT') or getattr(settings, 'MEDIA_ROOT', '') or '')
        if not root.strip():
            # an empty path is the working directory, which is not anybody's media
            msg = f"{self.label}['ROOT'] is empty and so is MEDIA_ROOT"
            raise MisconfiguredError(msg)
        return Path(root).expanduser()

    def entries(self) -> Iterator[MediaEntry]:
        """Regular files under ``ROOT``, sorted; an unreadable one is logged and left out."""
        root = self.root
        if not root.is_dir():
            logger.warning('Media directory not found', extra={'ob_path': str(root)})
        for directory, dirs, files in os.walk(root):
            dirs.sort()
            for name in sorted(files):
                path = Path(directory) / name
                try:
                    stat = path.lstat()
                except OSError as error:
                    logger.warning('Media file not listed', extra={'ob_path': str(path), 'ob_error': str(error)})
                    continue
                if not path.is_file() or path.is_symlink():
                    continue
                if not os.access(path, os.R_OK):
                    if self.strict:
                        msg = f'{self.label}: {path} cannot be read'
                        raise SourceMissingError(msg)
                    logger.warning('Media file not readable; left out', extra={'ob_path': str(path)})
                    continue
                yield MediaEntry(path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime)

    @contextlib.contextmanager
    def open(self, entry: MediaEntry) -> Iterator[tuple[IO[bytes], int]]:
        """The file and its size now — it may have changed since it was listed."""
        with (self.root / entry.name).open('rb') as handle:
            yield handle, os.fstat(handle.fileno()).st_size

    def check(self) -> list[checks.CheckMessage]:
        """``ROOT`` is set: whether it exists is the deploy check's question (see ``deploy_check``)."""
        found = super().check()
        try:
            _ = self.root
        except MisconfiguredError as error:
            found.append(checks.Error(str(error), id='offsite_backup.E001'))
        return found

    def deploy_check(self) -> list[checks.CheckMessage]:
        """W005: ``ROOT`` is missing where a pass runs; a web container without the mount is not warned."""
        found = super().deploy_check()
        try:
            root = self.root
        except MisconfiguredError:
            return found  # the settings check reports it
        if not root.is_dir():
            found.append(
                checks.Warning(
                    f'{self.label}: media directory {str(root)!r} does not exist here.',
                    hint='Mount the media volume into the container that runs the backup.',
                    id='offsite_backup.W005',
                )
            )
        return found
