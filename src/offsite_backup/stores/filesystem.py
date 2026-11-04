"""A directory as a store: a host path, or a Docker volume mounted into the container.

No immutability: whoever can write here can delete. Kept on the machine it protects, it
answers "somebody dropped a table", not "the machine is gone" — W001 says so on start.
"""

import os
import shutil
from datetime import datetime
from pathlib import Path

from offsite_backup import findings as checks
from offsite_backup.stores.base import Store, Version, Wrote

__all__ = ('DirectoryStore',)

_VERSION = 'null'


class DirectoryStore(Store):
    """``ROOT`` holds ``<PREFIX>/<kind>/<name>`` files; writes are atomic, files 0600."""

    REQUIRED = ('ROOT',)
    SIGNALS = ('ROOT',)

    @property
    def root(self) -> Path:
        """The directory every key is relative to."""
        return Path(str(self.option('ROOT'))).expanduser()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            msg = f'{key!r} leaves the store root'
            raise ValueError(msg)
        return path

    def put(self, path: Path, key: str, *, now: datetime | None = None) -> Wrote:
        """Copy to ``<key>.part`` then rename, so a reader never sees half a file."""
        target = self._path(key)
        for directory in reversed([target.parent, *target.parent.parents]):
            if not directory.exists():
                directory.mkdir(mode=0o700)
        partial = target.with_name(target.name + '.part')
        with path.open('rb') as source, partial.open('wb') as sink:
            shutil.copyfileobj(source, sink, 1 << 20)
            sink.flush()
            os.fsync(sink.fileno())
        partial.chmod(0o600)
        partial.replace(target)
        return Wrote(key=key, size=target.stat().st_size)

    def copy(self, source: str, key: str, *, now: datetime | None = None) -> Wrote:
        """A plain file copy."""
        return self.put(self._path(source), key, now=now)

    def versions(self, prefix: str) -> dict[str, list[Version]]:
        """One version per file; a directory store keeps no history."""
        base = self.root.resolve()
        found: dict[str, list[Version]] = {}
        start = self._path(prefix.rsplit('/', 1)[0]) if '/' in prefix else base
        if not start.is_dir():
            return found
        for path in sorted(start.rglob('*')):
            if not path.is_file() or path.name.endswith('.part'):
                continue
            key = path.relative_to(base).as_posix()
            if key.startswith(prefix):
                found[key] = [Version(_VERSION, latest=True, size=path.stat().st_size)]
        return found

    def delete(self, key: str, version: Version) -> None:
        """Remove the file."""
        self._path(key).unlink(missing_ok=True)

    def get(self, key: str, into: Path) -> None:
        """Copy the file out."""
        shutil.copyfile(self._path(key), into)

    def check(self) -> list[checks.CheckMessage]:
        """W001, always: this store keeps no immutable copy."""
        return [
            *super().check(),
            checks.Warning(
                f'{self.label} is a directory: backups there can be deleted by whoever can write to it.',
                hint='Fine for a fast local copy; keep the disaster copy in an S3 store with Object Lock.',
                id='offsite_backup.W001',
            ),
        ]
