# Stores: directory

```python
'STORE': {
    'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore',
    'ROOT': '/backups',
},
```

A directory on disk, or a Docker volume mounted into the container that runs the
backup. No extra, no driver.

The same object names as on S3 (`<ROOT>/<PREFIX>/<kind>/<stamp>...`), the same
encryption, the same [whole-night rotation](Retention.md), the same morning check.

## What it is not

**No immutability.** Whoever can write to the directory can delete from it — the
application, anyone who gets a shell in its container, and a mistaken `rm -r`. Kept on
the machine it protects, it answers "somebody dropped a table", not "the machine is gone"
and not "somebody got in". `manage.py check` says so on every start:

```text
?: (offsite_backup.W001) OFFSITE_BACKUP['STORE'] is a directory: backups there can be deleted by whoever can write to it.
	HINT: Fine for a fast local copy; keep the disaster copy in an S3 store with Object Lock.
```

That is a warning, not an error, on purpose: a directory store is a legitimate choice for
a staging machine, or for a disk that is in fact mounted from elsewhere. Silence it with
`SILENCED_SYSTEM_CHECKS = ['offsite_backup.W001']` once you have decided.

A project has one store. Several at once — a local volume for a fast restore and
S3 for disaster, each with its own retention — is on the roadmap.

## How files are written

* Each object is copied to `<name>.part`, flushed to disk, set to mode `0600`, and renamed
  into place, so a reader never sees half a file; a `.part` left by a crash is ignored by
  listings and rotation.
* Every directory the store creates, `ROOT` included, gets mode `0700`; directories that
  already exist keep theirs.
* A key that would resolve outside `ROOT` is refused.
* A directory keeps no history: one "version" per file, and a delete removes it.

## Docker volume

```yaml
services:
  web:
    volumes:
      - backups:/backups
  worker:
    volumes:
      - backups:/backups
volumes:
  backups: {}
```

Mount it into **every** service that runs a pass — the worker that runs the nightly task
and the service the deploy pass runs in — or each writes its own, unseen half.
