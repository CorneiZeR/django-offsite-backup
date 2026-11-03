#!/usr/bin/env bash
# Install the built wheel into a throwaway environment and check what a consumer gets:
# the modules, py.typed, the commands, the pytest plugin, and a Django boot with backups off.
# The unit suite imports from src/, so only an install catches a module left out of the wheel.
set -euo pipefail
unset PYTHONPATH

dist="$(cd "${1:?usage: smoke_install.sh DIST_DIR}" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

wheel="$(ls "$dist"/*.whl)"
sdist="$(ls "$dist"/*.tar.gz)"

python - "$wheel" "$sdist" <<'PY'
import sys, tarfile, zipfile

names = set(zipfile.ZipFile(sys.argv[1]).namelist())
for expected in (
    'offsite_backup/py.typed',
    'offsite_backup/pass_.py',
    'offsite_backup/stores/s3.py',
    'offsite_backup/testing/pytest_plugin.py',
    'offsite_backup/management/commands/backup_offsite.py',
    'offsite_backup/management/commands/backup_restore.py',
):
    assert expected in names, f'{expected} missing from the wheel'
assert not any(name.endswith('DESIGN.md') for name in names), 'DESIGN.md must not ship'
with tarfile.open(sys.argv[2]) as archive:
    members = {name.split('/', 1)[1] for name in archive.getnames() if '/' in name}
for expected in ('src/offsite_backup/__init__.py', 'tests/test_pass.py', 'docs/wiki/index.md', 'LICENSE'):
    assert expected in members, f'{expected} missing from the sdist'
assert 'DESIGN.md' not in members, 'DESIGN.md must not ship'
print('wheel and sdist carry what they should')
PY

python -m venv "$work/venv"
"$work/venv/bin/pip" install -q "$wheel"
mkdir -p "$work/project"
cat > "$work/project/smoke_settings.py" <<'PY'
SECRET_KEY = 'smoke'
INSTALLED_APPS = ['offsite_backup']
DATABASES = {}
PY
cd "$work/project"
export DJANGO_SETTINGS_MODULE=smoke_settings PYTHONPATH="$work/project"
"$work/venv/bin/django-admin" check
"$work/venv/bin/django-admin" backup_offsite | grep -q 'nothing to do'
"$work/venv/bin/django-admin" backup_offsite --help >/dev/null
"$work/venv/bin/python" -c 'import offsite_backup; print("installed", offsite_backup.__version__)'
echo 'smoke install passed'
