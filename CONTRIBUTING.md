# Contributing

## Set up

```shell
uv venv
uv pip install -e '.[all]' --group dev      # pip 25.1+ also understands --group
```

## The gate

```shell
ruff check . && ruff format --check . && mypy && python -m pytest -q
```

The unit suite needs no network and no binary beyond a POSIX shell: it runs against an
in-memory S3 (`offsite_backup.testing.FakeS3`) and a pass-through sealer.

The command line's tests also run where Django is not installed at all — the core must not
need it:

```shell
uv venv /tmp/core && uv pip install --python /tmp/core -e '.[cli]' --group core-test
/tmp/core/bin/python -m pytest -q tests/test_cli.py tests/test_without_django.py
```

## Integration

A full cycle against real services — backup, restore into a clean database, row counts of
every table compared; media volumes byte for byte; Object Lock read back; rotation over real
versions and delete markers; the restore drill into `target`; and a plain server's pass and
drill from the command line, with `CONNECTIONS` and no Django setting.

```shell
docker run -d --rm --name ob-pg -e POSTGRES_USER=ob -e POSTGRES_PASSWORD=ob -e POSTGRES_DB=source \
  -p 5432:5432 postgres:17
docker exec ob-pg psql -U ob -d source -c 'CREATE DATABASE target'
docker run -d --rm --name ob-s3 -e MINIO_ROOT_USER=minio -e MINIO_ROOT_PASSWORD=minio12345 \
  -p 9000:9000 cgr.dev/chainguard/minio:latest server /data

OFFSITE_BACKUP_TEST_S3_ENDPOINT=http://127.0.0.1:9000 \
  python -m pytest -m integration --ds=tests.integration_settings tests/integration
```

It needs `age`, `age-keygen`, `zstd`, `pg_dump`, `psql` and `pg_restore` on PATH, the client
at least as new as the server. Variables: `OFFSITE_BACKUP_TEST_PG_{HOST,PORT,USER,PASSWORD,SOURCE,TARGET}`,
`OFFSITE_BACKUP_TEST_S3_{ENDPOINT,ACCESS_KEY,SECRET_KEY}`. Each run creates fresh buckets:
COMPLIANCE locks cannot be lifted, so do not point it at a store you care about.

## Pull requests

One change per pull request, with a test that fails without it, and the wiki page that
describes the behaviour updated in the same pull request (`docs/wiki/`, built with
`mkdocs build --strict`). Add a line to `CHANGELOG.md`.

## Releasing

Bump `__version__` in `src/offsite_backup/__init__.py`, move the changelog section out of
"Unreleased", merge, then tag `vX.Y.Z` on master and push the tag. `publish.yml` checks the tag
against `__version__`, builds, smoke-installs the wheel, publishes to PyPI by trusted
publishing and creates the GitHub release.
