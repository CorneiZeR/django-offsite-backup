"""A tar of listed paths, plus the deployment's environment rebuilt from a list of names.

The live environment file may be unreadable here (another owner, a read-only mount), and
copying it opportunistically would carry whatever else it holds. Its values reached this
process through the environment; the *names* come from the list the deploy rendered it
from — so rebuilding it is a reconstruction, not a guess.
"""

import fnmatch
import io
import logging
import os
import tarfile
import time
from pathlib import Path
from string import ascii_letters, digits

from offsite_backup import conf
from offsite_backup import findings as checks
from offsite_backup.config.base import ConfigSource
from offsite_backup.exceptions import SourceMissingError
from offsite_backup.pipeline import feed
from offsite_backup.sealing import Sealing

__all__ = ('DEFAULT_EXCLUDE', 'REBUILT_FILE', 'PathsSource', 'env_line', 'rendered_environment')

logger = logging.getLogger('offsite_backup')

#: the rebuilt file's name in the archive: a restore sees at a glance it was reconstructed
REBUILT_FILE = 'rendered.env'
#: why a handed symlink fails the pass
_LINK = 'it is a symbolic link: the archive would hold the link, not its target'
#: the live environment file never travels; the rebuilt one stands in for it
DEFAULT_EXCLUDE = ('.' + 'env',)
#: a value needing no quotes for either reader of the file (compose and dotenv-style shells)
_BARE = frozenset(ascii_letters + digits + '_@%+=:,./-')


def env_line(name: str, value: str) -> str:
    """One ``NAME=value``, quoted so compose and a shell read back the same text."""
    if '\n' in value:
        # it would become extra lines: a secret holding one would forge settings nobody set
        return f'# {name} contains a newline, which this format cannot carry'
    if value and not set(value) - _BARE:
        return f'{name}={value}'
    if "'" in value:
        # inside double quotes both readers expand $NAME; a backslash keeps it literal for both
        escaped = value.replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$').replace('`', '\\`')
        return f'{name}="{escaped}"'
    return f"{name}='{value}'"


def rendered_environment(names_file: Path) -> str:
    """The environment rebuilt for every name listed in ``names_file`` (``#`` comments allowed)."""
    lines = [f'# Rebuilt from the running process, not copied. Names from {names_file.name}.']
    for row in names_file.read_text().splitlines():
        name = row.split('#', 1)[0].split()
        if not name:
            continue
        value = os.environ.get(name[0])
        # unset is written down: "this deployment ran without it" is what a restore needs
        lines.append(f'# {name[0]} was not set' if value is None else env_line(name[0], value))
    return '\n'.join(lines) + '\n'


class PathsSource(ConfigSource):
    """``PATHS`` (files or directories), ``RENDER_ENV`` (a names file), ``EXCLUDE`` globs (added to ``.env``)."""

    @property
    def paths(self) -> list[Path]:
        """The listed paths."""
        return [Path(path).expanduser() for path in self.strings('PATHS')]

    @property
    def names_file(self) -> Path | None:
        """``RENDER_ENV``, relative to ``BASE_DIR`` (off Django, the configuration file's directory) unless absolute."""
        value = self.option('RENDER_ENV')
        if not value:
            return None
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = conf.base_dir() / path
        return path

    def _excluded(self, path: Path, root: Path | None = None) -> bool:
        # added to the default, never replacing it: a custom list must not let the live file travel
        patterns = [*DEFAULT_EXCLUDE, *self.strings('EXCLUDE')]
        excluded = any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns)
        if excluded and root is not None and root in self.handed:
            msg = f'{self.label}: {path} was handed over to go up, and EXCLUDE leaves it out'
            raise SourceMissingError(msg)
        return excluded

    @property
    def handed(self) -> list[Path]:
        """``HANDED``: paths a caller added for one pass (``--path``) and may delete once it returns.

        All of each must go up, or the pass fails: missing, unreadable, or excluded.
        """
        return [Path(path).expanduser() for path in self.strings('HANDED')]

    @property
    def required_paths(self) -> list[Path]:
        """``REQUIRED: True`` (or ``STRICT``) means every path; a list names some of them; ``HANDED`` always."""
        if self.required:
            return self.paths
        if self.handed:
            listed = self.option('REQUIRED')
            named = [Path(str(path)).expanduser() for path in listed] if isinstance(listed, (list, tuple)) else []
            return [*named, *self.handed]
        listed = self.option('REQUIRED')
        if isinstance(listed, (list, tuple)):
            return [Path(str(path)).expanduser() for path in listed if str(path).strip()]
        return []

    def _unreadable(self, path: Path, root: Path) -> bool:
        if os.access(path, os.R_OK):
            return False
        # a handed path is all there or the pass fails: one left out silently would read as gone up
        if self.strict or root in self.handed:
            msg = f'{self.label}: {path} cannot be read'
            raise SourceMissingError(msg)
        return True

    def archive(self, into: Path, sealing: Sealing) -> Path:
        """Each path under its own name (``deploy/compose.yml``), the rebuilt environment at the root."""
        missing = [str(path) for path in self.required_paths if not path.exists()]
        if missing:
            msg = f'{self.label}: required path(s) missing: {", ".join(missing)}'
            raise SourceMissingError(msg)
        for gone in self.missing_optional():
            logger.warning('Configuration path not found; left out', extra={'ob_path': gone})
        with feed(sealing.stages(), into=into) as stdin, tarfile.open(fileobj=stdin, mode='w|') as tar:
            names = self.names_file
            if names is not None and names.is_file():
                data = rendered_environment(names).encode()
                member = tarfile.TarInfo(REBUILT_FILE)
                member.size = len(data)
                member.mtime = int(time.time())
                member.mode = 0o600
                tar.addfile(member, io.BytesIO(data))
            for root in self.paths:
                if root in self.handed:
                    self._add_handed(tar, root)
                else:
                    self._add_listed(tar, root)
        return into

    def _add_listed(self, tar: tarfile.TarFile, root: Path) -> None:
        """One of ``PATHS``: what can be read and is not excluded; a missing one is left out."""
        if root.is_file():
            if not self._excluded(root, root) and not self._unreadable(root, root):
                tar.add(root, arcname=root.name, recursive=False)
            return
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not path.is_file() or self._excluded(path, root):
                continue
            if self._unreadable(path, root):
                continue
            tar.add(path, arcname=f'{root.name}/{path.relative_to(root).as_posix()}', recursive=False)

    def _add_handed(self, tar: tarfile.TarFile, root: Path) -> None:
        """A handed path, all of it or the pass fails: its caller deletes it once the pass returns.

        Every regular file goes in; anything that would not — a symlink (the archive would hold the
        link, not what it points at), a folder that cannot be listed, a file that cannot be read or
        that ``EXCLUDE`` leaves out, a socket or a device — fails the pass by name.
        """
        if root.is_symlink() or not root.is_dir():
            self._add_handed_file(tar, root, root, root.name)
            return

        def unlisted(error: OSError) -> None:
            self._refuse(error.filename, f'it cannot be listed ({error.strerror})')

        for folder, dirs, files in os.walk(root, onerror=unlisted):
            here = Path(folder)
            for name in dirs:
                if (here / name).is_symlink():
                    self._refuse(here / name, _LINK)
            dirs.sort()
            for name in sorted(files):
                path = here / name
                self._add_handed_file(tar, root, path, f'{root.name}/{path.relative_to(root).as_posix()}')

    def _add_handed_file(self, tar: tarfile.TarFile, root: Path, path: Path, arcname: str) -> None:
        if path.is_symlink():
            self._refuse(path, _LINK)
        if not path.is_file():
            self._refuse(path, 'it is neither a regular file nor a folder')
        self._excluded(path, root)
        self._unreadable(path, root)
        tar.add(path, arcname=arcname, recursive=False)

    def _refuse(self, path: Path | str, why: str) -> None:
        msg = f'{self.label}: {path} was handed over to go up, and {why}'
        raise SourceMissingError(msg)

    def missing_optional(self) -> list[str]:
        """The listed paths and the names file not found here, the required ones aside."""
        required = set(self.required_paths)
        missing = [str(path) for path in self.paths if not path.exists() and path not in required]
        names = self.names_file
        if names is not None and not names.is_file():
            missing.append(str(names))
        return missing

    def deploy_check(self) -> list[checks.CheckMessage]:
        """E009 for a required path, W005 for the rest: only where a pass runs, never on every ``manage.py``."""
        found = super().deploy_check()
        required = [str(path) for path in self.required_paths if not path.exists()]
        if required:
            found.append(
                checks.Error(
                    f'{self.label}: required path(s) missing here: {", ".join(required)}.',
                    hint='Mount the deployment directory into this container, or drop REQUIRED for it.',
                    id='offsite_backup.E009',
                )
            )
        missing = self.missing_optional()
        if missing:
            found.append(
                checks.Warning(
                    f'{self.label}: {", ".join(missing)} not found here.',
                    hint='Mount the deployment directory (read-only) into every container that runs the backup.',
                    id='offsite_backup.W005',
                )
            )
        return found
