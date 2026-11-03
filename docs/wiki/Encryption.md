# Encryption and keys

```python
'ENCRYPTION': {
    'BACKEND': 'offsite_backup.encryption.age_cli.AgeCli',
    'RECIPIENTS': os.environ.get('BACKUP_AGE_RECIPIENT', ''),
},
```

Every object is encrypted with [age](https://age-encryption.org) to one or more **public**
keys. The server holds only public keys, so it can write a backup and cannot read one.
The private half lives off the machine — in your password manager, and nowhere else.

## Making a key pair

On your own machine, not the server:

```shell
age-keygen -o backup-acme-prod.key
# Public key: age1qyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqszqgpqyqs3290gq
```

* The **public key** (`age1…`) goes into the server's environment, e.g.
  `BACKUP_AGE_RECIPIENT`. It is not a secret.
* The **file** holds the private key (`AGE-SECRET-KEY-1…`). Put it in your password
  manager, then delete the file. `age-keygen -y backup-acme-prod.key` prints the public
  key again from it.

One key pair per product and environment keeps a leaked key's reach small. Losing the
private key loses every backup sealed to it — store it where at least two people can
reach it.

## `RECIPIENTS`

One public key, or a list. A comma-separated string is **not** split — read several from
the environment yourself:

```python
'RECIPIENTS': [r for r in os.environ.get('BACKUP_AGE_RECIPIENTS', '').split(',') if r.strip()],
```

With several recipients, **any one** of the private keys opens a backup — a second key for
a colleague, or a break-glass key in a safe.

age's SSH recipients (`ssh-ed25519 …`, `ssh-rsa …`) are accepted too.

`manage.py check` refuses anything else in `RECIPIENTS` as `E008` — above all a private
key, which would put the means to read every backup on the machine they protect:

```text
?: (offsite_backup.E008) OFFSITE_BACKUP['ENCRYPTION']['RECIPIENTS'] holds a PRIVATE age key.
	HINT: Put the public half (age1...) here; the private half belongs off this machine.
```

## Compress, then encrypt

The order is not a preference: an encrypted stream does not compress. Databases and the
configuration archive go `source | zstd | age`; media volumes go `tar | age`, because
images and video are compressed already.

```python
'COMPRESS': ['zstd', '-19', '--long=27', '-T0', '-q'],   # the default
'DECOMPRESS': ['zstd', '-d', '-q', '--long=31'],         # what a restore runs
```

`-19 --long=27` is slow and small, which suits a batch job at night. A large database that
does not finish in its window is better served by `-10` than by no compression; an empty
`COMPRESS` turns the stage off. `DECOMPRESS` must accept the window `COMPRESS` used —
`--long=31` accepts any. A restore decides whether to decompress from zstd's magic bytes,
not from the name.

Every stage runs under `pipefail` semantics: a stage that dies mid-stream fails the pass
instead of leaving a small, valid, truncated file.

## A second key for one dump: the wake identity

Some databases must be restorable **by the server itself** — a tenant put to sleep and
woken on demand. Seal that one dump to an extra recipient whose private key the server
holds, and keep every other backup readable off the machine only:

```python
from offsite_backup import back_up_database, restore_database

key = back_up_database('tenant_42', extra_recipients=[settings.WAKE_RECIPIENT])
...
restore_database(key, 'tenant_42', identity=settings.WAKE_IDENTITY_FILE)
```

The server's wake key opens those dumps and nothing else. Treat it as a secret of the
server, with the same care as its database password.

## Not in 0.1.0

A pure-Python backend (`pyrage`, no `age` binary), an explicit "no encryption" backend, and
helpers to re-seal recent backups to a new recipient are on the roadmap. For tests,
`offsite_backup.testing.Passthrough` stands in for `age` — and raises `W002` so it is never
mistaken for encryption ([Testing](Testing.md)).
