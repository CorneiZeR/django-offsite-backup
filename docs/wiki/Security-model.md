# Security model

The design goal in one line: **the machine that writes a backup cannot read one, and
cannot destroy one it has written.** This page says how far that holds, and where it stops.

## What sits where

| | where | secret? |
| --- | --- | --- |
| age **public** key(s) | server environment (`RECIPIENTS`) | no |
| age **private** key | your password manager, off the machine | **yes** — opens every backup |
| S3 key for the store | server environment | yes — writes, lists, reads ciphertext |
| S3 key for an S3 media source | server environment | yes — reads the media bucket |
| notifier token | server environment, or django-aiogram's settings | yes |
| database password | server environment; `PGPASSWORD` for `pg_dump`, a 0600 option file for `mysqldump` | yes |
| the Docker socket (a `VolumeSource` only) | mounted into the worker | **yes** — root on the host |
| a wake key (optional) | server | yes — opens only the dumps sealed to it |

## What the server can and cannot do

The server — the application, its worker, anyone who has a shell on it — **can**:

* write new backups, list the store, download ciphertext;
* read the media, the databases, the deployment directory and the environment, because
  it backs them up;
* stop backing up.

It **cannot**:

* **read a backup.** Everything is sealed with age to public keys; the private key is not
  on the machine, and `E008` refuses one in `RECIPIENTS`.
* **delete or shorten a COMPLIANCE-locked object** before its lock runs out — not with the
  key it has — and, on providers that implement COMPLIANCE as AWS does, not with the
  account's root credentials either. That is what COMPLIANCE means, and why it is the only
  mode this package uses.
* **send backups somewhere else by accident.** The S3 client is built from the configured
  options only; ambient credentials (`~/.aws`, instance roles, `AWS_*`) are ignored.

## Threat model

### A stolen store key (the server is compromised, or a key leaks)

* **Cannot read backups**: ciphertext only.
* **Cannot delete a locked object** within its lock — 13 days by default. Deleting the key
  name only adds a delete marker; the locked version stays and can be retrieved by version
  id with the provider's tools.
* **Can add things on top**: new versions of a key or delete markers. `backup_restore`
  reads the *current* version, so after a compromise list the versions
  (`aws s3api list-object-versions`) and restore from a version written before it. The
  morning check only sees current versions, so hiding the recent backups behind markers
  makes it report them **missing**.
* **Can delete what is no longer locked**: everything older than `LOCK_DAYS`. The window
  you can count on after a compromise is `LOCK_DAYS`, not `KEEP_NIGHTS`. Notice early —
  that is what the [morning check](Monitoring.md) is for — and raise `LOCK_DAYS` (with the
  bucket's default rule and `KEEP_NIGHTS`) if two weeks is too short for you.
* **Can fill the bucket** and so the bill. Provider-side quotas and billing alerts are the
  answer.
* **On a provider where keys reach every bucket of their project**, can do all of the above
  to every bucket in that project. Hence one project per product or client
  ([S3 providers](S3-providers.md)).

### GOVERNANCE mode

Bypassable by any key holding the "bypass governance retention" permission — on some
providers, any key at all. It protects against mistakes, not against an attacker with the
server's key. This package never uses it, and the read-back fails a pass where an object
lands GOVERNANCE.

### A directory store

**No immutability.** Anyone who can write to the directory can delete every backup in it,
and it is on the machine it protects. It protects against a dropped table, not against a
lost machine or an intruder (`W001`).

### `LOCK_DAYS: 0`

The same as a directory store, off the machine: encrypted, and deletable by the server's
key.

### The private key

Whoever has it can read every backup sealed to it — including the configuration archive,
which holds every secret the application has (`rendered.env`). Keep it off servers, keep it
where two people can reach it, and restore from your own machine. If it ever reached a
server, make a new pair and treat the old backups as exposed.

### The drill's key

The [restore drill](Restore.md#the-monthly-drill) decrypts, so wherever it runs holds a
private key. Run it on a machine of its own, with a read-only store key and a scratch
database, and the production server stays unable to read a backup. Scheduled on the
production worker instead, the worker can read every backup sealed to that key — a choice
to make knowingly, with a recipient that exists only for the drill and can be rotated on its
own. The drill refuses a target that is a live database, and empties its target afterwards,
so a copy of production does not stay in the scratch database.

### The wake key

A server that holds an extra identity can open the dumps sealed to it — by design, so it
can restore them itself — and nothing else. Seal only the dumps that need it.

### The Docker socket

A `VolumeSource` reads a volume the worker does not mount, through the Docker daemon — so
the worker holds the daemon's socket, and whoever controls the worker can start any
container on the host: **root on the host**. The source itself only creates a container
(never starts it), mounts the volume read-only with no network, and reads it with
`docker cp`; the risk is the socket, not what the source does with it. Mount the volume
read-only into the worker and use `DirectorySource` where you can; where you cannot, a
socket proxy that allows only `volume inspect`, `create`, `archive` (what `docker cp` uses)
and `rm` narrows it.

### Several stores

A second store is a second copy, not a second lock: a `DirectoryStore` beside S3 adds a
fast restore and an unlocked copy on the machine. The off-site, locked store is what
survives an intruder; a pass that could not write to it fails by its name.

### What leaves the machine besides backups

* Messages carry the prefix, sizes, durations and, on failure, the first 500 characters of
  the error — escaped. Errors never include the database password: it travels in
  `PGPASSWORD` (PostgreSQL) or a 0600 option file (MySQL, MariaDB), never on a command line. A Telegram transport error is logged by its class
  only, because its text contains the URL and therefore the token.
* A webhook receives the outcome as data — the prefix, keys, sizes, the error text — and
  never a credential; with a `SECRET` the body is signed so the receiver can refuse a forged
  one. Its URL is never logged.
* No message, token, password or private key is logged — the HTTP client's own request line
  included: the notifiers' token and webhook URL are redacted from it.

### Tests

Notifiers that reach the network are silenced for every pytest run and under
`OffsiteBackupTestRunner` (or `OffsiteBackupRunnerMixin` in a project's own runner),
`--parallel` and pytest-xdist workers included. Keep production tokens out of CI test jobs anyway
([Testing](Testing.md)).

## Reporting a vulnerability

Please report security issues privately through GitHub's security advisories for the
repository rather than in a public issue. See `SECURITY.md` in the repository.
