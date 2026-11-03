# Configuration archive

```python
'CONFIG': {
    'BACKEND': 'offsite_backup.config.paths.PathsSource',
    'PATHS': ['/app/deploy'],
    'RENDER_ENV': 'deploy-vars.txt',
},
```

What it takes to rebuild the machine rather than the data: the compose file, the
Dockerfile, the reverse-proxy configuration, certificates, signing keys — and the
environment the deploy gave the application. One archive, `tar | zstd | age`, written
**last** in every pass.

## Why last

A configuration object for a stamp means every part before it went up. The
[morning check](Monitoring.md) relies on that: it reads the `config/` folder (and every
database's) for the newest whole backup, so a pass that died half way cannot pass for a
night.

## `PATHS`

Files and directories. A directory is archived under its own name, recursively; a file at
the archive's root:

```text
PATHS = ['/app/deploy', '/etc/acme/signing.pem']

deploy/compose.yml
deploy/Caddyfile
deploy/certs/node.pem
signing.pem
rendered.env
```

Inside a directory, symbolic links are skipped; files this process cannot read are
skipped wherever they are. Files whose **name**
matches `.env` or a glob in `EXCLUDE` are left out. `EXCLUDE` adds to `.env`, never
replaces it: the live environment file does not travel whatever the list says.

A path that does not exist on this machine is `W005` — almost always a mount missing
from one of the containers that run a pass. Blank items are dropped, so
`os.environ.get('X', '')` in the list is harmless where `X` is unset.

## Required paths

By default a missing path is left out with a warning and the pass goes on. When the
certificates or signing keys are part of what restores the machine, a pass without them
is not a whole backup — make them required:

```python
'CONFIG': {
    'BACKEND': 'offsite_backup.config.paths.PathsSource',
    'PATHS': ['/app/deploy', '/etc/acme/signing.pem'],
    'REQUIRED': ['/etc/acme/signing.pem'],   # or True: every path in PATHS
},
```

* A required path missing at pass time **fails the pass** with `SourceMissingError`, and
  the FAILED message names it. The databases and media written before it stay written; the
  configuration archive — and so the night, for the [morning check](Monitoring.md) — does
  not.
* `manage.py check --deploy` reports it as `E009` in the container it runs in. A plain
  `manage.py check` does not: it runs in every container, most of which never run a pass,
  and an error there would stop `migrate` ([why](System-checks.md#why-e009-is-a-deploy-check)).
* `STRICT: True` at the top level makes every path required, and a file that cannot be
  read fails the pass instead of being skipped ([Settings](Settings.md#strict-and-required)).

Use `REQUIRED` for what a restore cannot do without; leave paths that may legitimately be
absent on some machines optional.

## The live `.env` does not travel; a rebuilt one does

The live environment file is often unreadable to the application (another owner, a
read-only mount), and copying it opportunistically would carry whatever else it holds.
Its values already reached this process through the environment; what is missing is the
list of *names*. Give that list in `RENDER_ENV`:

```text
# deploy-vars.txt — one name per line, comments allowed
DJANGO_SECRET_KEY
DATABASE_URL
BACKUP_S3_ACCESS_KEY
BACKUP_S3_SECRET_KEY
SENTRY_DSN   # optional in staging
```

and the archive gets `rendered.env` at its root, rebuilt from the running process:

```shell
# Rebuilt from the running process, not copied. Names from deploy-vars.txt.
DJANGO_SECRET_KEY='s3cr3t$with spaces'
DATABASE_URL=postgres://acme:pw@db:5432/acme
BACKUP_S3_ACCESS_KEY=AKIA...
BACKUP_S3_SECRET_KEY=...
# SENTRY_DSN was not set
```

* A name that is not set is written down as such: "this deployment ran without it" is what
  a restore needs to know.
* Values are quoted so that Docker Compose and a POSIX shell read back the same text.
  Plain values stay bare, anything else is single-quoted, and a value holding a single
  quote is double-quoted with `\`, `"`, `$` and `` ` `` escaped.
* A value holding a newline is **not** written — it would become extra lines, and a secret
  holding one could forge settings nobody set. A comment says it was skipped.

`RENDER_ENV` is relative to `settings.BASE_DIR` unless absolute. If your deploy renders the
`.env` from a list of variable names already, point `RENDER_ENV` at that same list.

The archive therefore holds every secret the application has. It is sealed to your age
recipient like everything else; whoever holds the private key holds all of it.

## Mount the deployment directory read-only — everywhere a pass runs

```yaml
services:
  web:
    volumes:
      - ./:/app/deploy:ro
  worker:
    volumes:
      - ./:/app/deploy:ro
```

The worker runs the nightly pass; the deploy pass runs in the service your deploy starts
it in (often the web image as a one-off). Both need the mount, or one of them archives an
empty folder and `W005` tells you which — or, for a required path, the pass fails and
`check --deploy` says `E009`.

## Without `CONFIG`

Leave it out and the morning check reads the databases' folders alone. A project with
neither `CONFIG` nor `DATABASES` is checked on its media folders.
