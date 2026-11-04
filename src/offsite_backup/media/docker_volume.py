"""Media in a named Docker volume that is not mounted where the backup runs, read through the Docker CLI.

A throwaway container is *created* — never started — with the volume mounted read-only
and no network, and ``docker cp <container>:/volume/. -`` streams the volume as one tar
through the daemon: nothing in the image runs, so any small image will do. That one stream
is cut into volumes as it goes (a file's size is in its header before its bytes), so the
volume is read once, and nothing but the sealed volume being written touches the disk.
The container is removed afterwards, whatever happened.

The worker needs the ``docker`` CLI and the daemon's socket (``DOCKER_HOST`` is honoured):
access to that socket is root on the host, so prefer mounting the volume read-only into the
worker and reading it with ``DirectorySource`` wherever that is possible.
"""

import contextlib
import logging
import subprocess
import tarfile
from collections.abc import Generator, Iterator
from typing import IO

from offsite_backup import findings as checks
from offsite_backup.exceptions import PipelineError, SourceMissingError
from offsite_backup.media.base import Item, MediaEntry, MediaSource
from offsite_backup.pipeline import Pipeline

__all__ = ('MOUNT', 'VolumeSource')

logger = logging.getLogger('offsite_backup')

#: where the volume is mounted in the throwaway container
MOUNT = '/volume'
#: what the container would run if anyone started it; nobody does
_COMMAND = 'true'
_DRAIN = 1 << 16


def _run(command: list[str]) -> str:
    """Run one ``docker`` command and answer its stdout; its stderr becomes the error."""
    try:
        done = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        msg = f'`{command[0]}` could not be started: {error.strerror or error}'
        raise PipelineError(msg) from error
    if done.returncode:
        msg = f'`{" ".join(command[:3])}` exited with status {done.returncode}: {done.stderr.strip()[-2000:]}'
        raise PipelineError(msg)
    return done.stdout.strip()


def _drain(stream: IO[bytes]) -> None:
    while stream.read(_DRAIN):
        pass


class VolumeSource(MediaSource):
    """``VOLUME`` (a named volume), ``IMAGE`` (default ``busybox:stable``; created, never started)."""

    REQUIRED = ('VOLUME',)
    BINARIES = ('docker',)

    @property
    def volume(self) -> str:
        """The named volume."""
        return str(self.option('VOLUME', '')).strip()

    @property
    def image(self) -> str:
        """The image the throwaway container is created from; pulled by ``docker create`` if absent."""
        return str(self.option('IMAGE') or 'busybox:stable')

    def exists(self) -> bool:
        """Whether the daemon knows the volume; raises when the daemon cannot be asked."""
        try:
            _run(['docker', 'volume', 'inspect', '--format={{.Name}}', self.volume])
        except PipelineError as error:
            if 'no such volume' in str(error).lower():
                return False
            raise
        return True

    @contextlib.contextmanager
    def _container(self) -> Iterator[str]:
        container = _run(
            [
                'docker',
                'create',
                '--network=none',
                '--label=offsite_backup=media',
                f'--volume={self.volume}:{MOUNT}:ro',
                self.image,
                _COMMAND,
            ]
        )
        try:
            yield container
        finally:
            with contextlib.suppress(PipelineError):
                _run(['docker', 'rm', '--force', container])

    def entries(self) -> Iterator[MediaEntry]:
        """Every regular file of the volume — read through once; a pass uses ``items()`` and reads it once only."""
        for item in self.items():
            yield item[0]

    def items(self) -> Generator[Item, None, None]:
        """Each regular file of the one ``docker cp`` stream, as it arrives; links and the rest are left out."""
        # `docker create` makes a missing named volume, empty, and a typo would then back up nothing
        if not self.exists():
            logger.warning('Media volume not found', extra={'ob_volume': self.volume})
            return
        with self._container() as container:
            command = ['docker', 'cp', f'{container}:{MOUNT}/.', '-']
            pipeline = Pipeline([command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
            stdout: IO[bytes] = pipeline.processes[-1].stdout  # type: ignore[assignment]
            try:
                with tarfile.open(fileobj=stdout, mode='r|') as archive:
                    for member in archive:
                        name = member.name.removeprefix('./').lstrip('/')
                        if not member.isreg() or not name:
                            self._not_regular(member, name)
                            continue
                        if not self.wanted(name):
                            continue
                        stream = archive.extractfile(member)
                        entry = MediaEntry(name, member.size, float(member.mtime))
                        yield entry, lambda stream=stream, size=member.size: contextlib.nullcontext((stream, size))  # type: ignore[misc]
                # tar stops reading at its end blocks; the rest must be read, or docker waits on a full pipe
                _drain(stdout)
            except tarfile.TarError:
                # an unreadable stream is most often docker failing: its own status says why
                _drain(stdout)
                pipeline.finish()
                raise
            except BaseException:
                pipeline.kill()
                raise
            pipeline.finish()

    def _not_regular(self, member: tarfile.TarInfo, name: str) -> None:
        if not member.islnk():
            return  # directories, symbolic links (never followed), devices
        if self.strict:
            msg = f'{self.label}: {name} is a hard link, which a volume streamed once cannot carry'
            raise SourceMissingError(msg)
        logger.warning('Media file is a hard link; left out', extra={'ob_path': name})

    def deploy_check(self) -> list[checks.CheckMessage]:
        """E012 when the daemon cannot be asked; W005 (E009 when required) when the volume does not exist."""
        found = super().deploy_check()
        if not self.volume:
            return found
        try:
            if self.exists():
                return found
        except PipelineError as error:
            found.append(
                checks.Error(
                    f'{self.label}: the Docker daemon could not be asked about {self.volume!r} ({str(error)[-300:]}).',
                    hint='Mount the Docker socket into the container that runs the backup, or set DOCKER_HOST.',
                    id='offsite_backup.E012',
                )
            )
        else:
            message = f'{self.label}: Docker volume {self.volume!r} does not exist here.'
            hint = 'Name the volume as `docker volume ls` shows it (compose prefixes the project name).'
            if self.required:
                found.append(checks.Error(message, hint=hint, id='offsite_backup.E009'))
            else:
                found.append(checks.Warning(message, hint=hint, id='offsite_backup.W005'))
        return found
