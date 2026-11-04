# AGENTS.md

Instructions for coding agents working on **this repository**. For wiring the package into a
project, see the wiki page [AI assistants](https://corneizer.github.io/django-offsite-backup/latest/AI-assistants/).

## What this is

A Django app that backs up databases, media and deployment configuration off the machine:
compress, then encrypt to public keys, upload to a store that locks every object, rotate by
whole nights, and say the outcome. Every backend is a dotted path in `OFFSITE_BACKUP` and is
imported only when named.

```text
src/offsite_backup/
    __init__.py     lazy exports (PEP 562), __version__
    apps.py         registers the checks, imports nothing else
    conf.py         the setting: defaults, on/off/partly-configured
    registry.py     SHIPPED (path -> driver, extra), build()
    backend.py      what every backend shares (REQUIRED, SIGNALS, BINARIES, checks)
    checks.py       system checks; network and mount ones are deploy checks
    naming.py       object names; the night is read from the name
    pipeline.py     subprocess pipelines with pipefail semantics, no shell
    sealing.py      compress THEN encrypt
    pass_.py        one pass, back_up_database, keep_apart
    rotation.py     whole-night rotation and depth
    guard.py        once per UTC night
    check.py        the morning check
    restore.py      download, decrypt, verify, load
    notify.py       formatter + notifiers; silence() and capture()
    outcome.py      the records messages are written from
    messages/       the shipped format
    notifiers/ databases/ media/ config/ encryption/ stores/   one backend per module
    schedule/       Celery and taskiq adapters, imported only by projects using them
    management/commands/   backup_offsite, backup_check, backup_list, backup_restore
    testing/        FakeS3, Passthrough, use_fake_s3, the runner and the pytest plugin
```

## Commands

```shell
uv venv && uv pip install -e '.[all]' --group dev
ruff check . && ruff format --check . && mypy && python -m pytest -q
```

Integration (real PostgreSQL, an S3 server with Object Lock, `age`, `zstd`; MySQL/MariaDB
apart, marked `mysql`) — see `CONTRIBUTING.md`:

```shell
OFFSITE_BACKUP_TEST_S3_ENDPOINT=http://127.0.0.1:9000 OFFSITE_BACKUP_TEST_PG_PORT=5432 \
  python -m pytest -m 'integration and not mysql' --ds=tests.integration_settings tests/integration
```

## Rules

- **Nothing happens at import time.** `import offsite_backup` loads no driver; every backend
  imports its driver inside the function that needs it. `tests/test_checks_commands.py`
  imports every module with every driver blocked.
- **No private references.** This package is public: no company, client, product, host or
  ticket names in code, docs, tests or commit messages. Examples use `acme/prod`.
- **Every change carries a test that fails without it.** Swap the source, watch it go red,
  put it back — never `git checkout` a file with uncommitted work. One case per guard; no
  case that fails for an earlier reason.
- **A notifier never raises and never costs the backup**; it logs the error class, never the
  text it sends or a token.
- **Secrets travel in the environment, never in argv**: argv is visible in `ps`, and error
  text is posted to a chat.
- **The lock is an argument of every upload and is read back.** Do not rely on a bucket
  default for a single PUT.
- **Rotation is scoped to `<prefix>/<kind>/` with the trailing slash**, counts nights read
  from names, deletes versions (never a bare key), and leaves locked versions for later.
- A settings rename or a default flip is a major version.

## Style

Code, comments, docstrings and docs in English. A comment is one line for the non-obvious
*why*; module docstrings explain design. Commits and pull requests carry no AI attribution.
