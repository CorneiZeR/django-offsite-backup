# Stores: S3

```python
'STORE': {
    'BACKEND': 'offsite_backup.stores.s3.S3Store',
    'ENDPOINT_URL': os.environ.get('BACKUP_S3_ENDPOINT', ''),
    'REGION': os.environ.get('BACKUP_S3_REGION', ''),
    'BUCKET': os.environ.get('BACKUP_S3_BUCKET', ''),
    'ACCESS_KEY': os.environ.get('BACKUP_S3_ACCESS_KEY', ''),
    'SECRET_KEY': os.environ.get('BACKUP_S3_SECRET_KEY', ''),
    'LOCK_DAYS': 13,
},
```

`pip install 'django-offsite-backup[s3]'`. Any S3-compatible server; what each provider
needs is on [S3 providers](S3-providers.md). Options are in [Settings](Settings.md).

## What the bucket must be

| | why | checked by |
| --- | --- | --- |
| **versioning** enabled | Object Lock needs it; it comes with Object Lock | `E005` |
| **Object Lock** enabled | the immutability itself — and it can only be switched on **when the bucket is created** | `E005` |
| a **default retention rule**: COMPLIANCE, `LOCK_DAYS` days | multipart uploads take their lock *date* from it (below) | `E006` |
| `KEEP_NIGHTS` greater than `LOCK_DAYS` | so rotation only ever meets objects whose lock has run out | `E007` |

`E005` and `E006` read the bucket, so they run under `python manage.py check --deploy`
only; a plain `manage.py` never reaches S3.

## How an object is written

Every upload passes the lock **as arguments** — `ObjectLockMode=COMPLIANCE` and
`ObjectLockRetainUntilDate=now + LOCK_DAYS` — and then **reads it back** with
`HeadObject`. The object must have landed COMPLIANCE and locked until at least the date
asked, less one hour of slack for clock skew. If it did not, the pass fails with
`BackupUnlockedError` *before* rotation runs, so an unprotected backup is never what
pushes a protected one out.

Each rule answers something measured on a real S3 provider:

* **A single PUT ignored the bucket's default retention.** The bucket's default read back
  as COMPLIANCE, and new objects still landed GOVERNANCE. So the mode and the date are
  arguments of every upload, never left to the bucket.
* **GOVERNANCE protects nothing** against the key that writes: that key can delete through
  it with "bypass governance retention", and some providers have no IAM to withhold the
  permission. Only COMPLIANCE. `LOCK_DAYS` sets the days; the mode is not configurable.
* **A multipart upload kept the mode but took its date from the bucket's default rule**,
  not from the request. Without a default rule of exactly `LOCK_DAYS` days, every object
  above one part — every media volume — fails the read-back. That is `E006`.
* **One PUT stops at 5 GB** (`EntityTooLarge`). Uploads go multipart in 64 MiB parts
  above 64 MiB, and media is cut into volumes of at most 4 GiB.
* **On a versioned bucket, a delete without a version id only writes a delete marker** —
  every byte stays stored and billed. Rotation lists and deletes *versions*, markers
  included.

The client retries each request up to five times (botocore's `standard` mode), and every
listing follows pagination to its end.

## Client options

Three options shape how the client talks to the server, on the store and on an `S3Source`
alike:

| option | default | |
| --- | --- | --- |
| `ADDRESSING_STYLE` | `'auto'` | `'path'` (`https://host/bucket/key`) or `'virtual'` (`https://bucket.host/key`). MinIO and SeaweedFS need `'path'` — [which provider needs which](S3-providers.md#addressing-style). Anything else is `E001` |
| `SIGNATURE_VERSION` | botocore's | e.g. `'s3v4'`, for a server that insists on one |
| `VERIFY` | `True` | `False` to skip TLS verification, or the path of a CA bundle for a server behind an internal certificate; a path that is not a file is `E001` |

## Credentials: only the ones you name

The client is built from `ENDPOINT_URL`, `REGION`, `ACCESS_KEY` and `SECRET_KEY` and
nothing else. botocore's default credential chain is not consulted: a stray `~/.aws`, an
instance role or an `AWS_*` variable would otherwise send backups somewhere nobody chose,
reporting success.

Give the backup its **own** key, allowed to reach this bucket (and, where the provider can
narrow it, this prefix) and nothing else. What it needs is in
[S3 providers](S3-providers.md#aws-s3).

## Keys and prefixes

```text
<PREFIX>/<kind>/<stamp>[.without-media][.<NNN>].<suffix>

acme/prod/db/2026-03-10T03-00Z.sql.zst.age
acme/prod/db-analytics/2026-03-10T03-00Z.sql.zst.age
acme/prod/media/2026-03-10T03-00Z.001.tar.age
acme/prod/media/2026-03-10T03-00Z.002.tar.age
acme/prod/config/2026-03-10T03-00Z.tar.zst.age
acme/prod/db/2026-03-10T14-12Z.without-media.sql.zst.age
acme/prod/config/2026-03-10T14-12Z.without-media.tar.zst.age
```

Several projects can share one bucket, a prefix each; rotation is strictly scoped to
`<PREFIX>/<kind>/` **with the trailing slash**, so `acme/prod/` never touches
`acme/prod-old/`. Whether they *should* share depends on what a key can reach — on some
providers a key reaches every bucket of its project, and then one leaked key exposes every
project in it. [S3 providers](S3-providers.md) says which.

## Without a lock

`LOCK_DAYS: 0` uploads without lock arguments and reads nothing back; `check --deploy`
then only checks the bucket is reachable. Use it for a bucket that cannot have Object
Lock — knowing that the key on the server can then delete every backup
([Security model](Security-model.md)).
