"""A ``docker`` CLI stand-in: volumes are directories under ``$FAKE_DOCKER_ROOT/volumes``.

It answers the four commands ``VolumeSource`` runs, logs each argv to ``calls.log``, and
streams ``docker cp`` the way the daemon does: one tar of the volume, names under ``./``.
"""

import json
import os
import sys
import tarfile
import uuid
from pathlib import Path

ROOT = Path(os.environ['FAKE_DOCKER_ROOT'])


def main(argv):  # noqa: PLR0911 - one answer per command
    with (ROOT / 'calls.log').open('a') as log:
        log.write(json.dumps(argv) + '\n')
    containers = ROOT / 'containers'
    containers.mkdir(exist_ok=True)
    if argv[:2] == ['volume', 'inspect']:
        name = argv[-1]
        if not (ROOT / 'volumes' / name).is_dir():
            sys.stderr.write(f'Error response from daemon: get {name}: no such volume\n')
            return 1
        sys.stdout.write(name + '\n')
        return 0
    if argv[0] == 'create':
        mount = next(arg for arg in argv if arg.startswith('--volume='))
        name = mount.removeprefix('--volume=').split(':')[0]
        container = uuid.uuid4().hex
        (containers / container).write_text(name)
        sys.stdout.write(container + '\n')
        return 0
    if argv[0] == 'cp':
        container, _ = argv[1].split(':', 1)
        if os.environ.get('FAKE_DOCKER_CP_FAILS'):
            sys.stderr.write('Error response from daemon: permission denied\n')
            return 1
        volume = ROOT / 'volumes' / (containers / container).read_text()
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as tar:
            tar.add(volume, arcname='.', filter=lambda member: member)
        return 0
    if argv[0] == 'rm':
        (containers / argv[-1]).unlink(missing_ok=True)
        return 0
    sys.stderr.write(f'fake docker: {argv!r} is not known\n')
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
