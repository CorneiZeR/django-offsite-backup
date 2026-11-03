# S3 providers

Whatever the provider, the bucket needs three things, and the first one is only possible
at creation:

1. **Object Lock, enabled when the bucket is created** (versioning comes with it);
2. a **default retention rule of COMPLIANCE, `LOCK_DAYS` days** — 13 unless you changed it;
3. a key for the backup alone.

Then `python manage.py check --deploy` on the server: no `E005`, no `E006`, and the bucket
is ready. Run a first pass with media (volumes go up multipart, which is what the default
rule is for) and look for `Backup written` before trusting it.

The examples use the AWS CLI, which talks to every provider below with `--endpoint-url`.

## Hetzner Object Storage

The provider this package's S3 rules were measured on.

**One Hetzner project per product or client.** Hetzner's S3 credentials belong to a
*project* and reach **every bucket in it**; they cannot be narrowed to a bucket or a
prefix. A key leaked from one product's server must not open another's backups, so each
product — and each client — gets its own project, its own bucket and its own key. Services
of your own that share a trust boundary may share one bucket, a `PREFIX` each.

1. Create the project, then the S3 credentials in it (*Security → S3 credentials*). The
   secret is shown once.

2. Create the bucket **with Object Lock**. It cannot be enabled later; a bucket created
   without it has to be replaced. With the AWS CLI:

    ```shell
    export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=...
    ENDPOINT=https://fsn1.your-objectstorage.com     # nbg1, hel1, ... — the bucket's location

    aws s3api create-bucket --bucket acme-backups \
        --object-lock-enabled-for-bucket --endpoint-url "$ENDPOINT"
    ```

    If you create it in the console instead, tick Object Lock in the creation dialog.

3. Set the default rule — **COMPLIANCE, days equal to `LOCK_DAYS`**:

    ```shell
    aws s3api put-object-lock-configuration --bucket acme-backups --endpoint-url "$ENDPOINT" \
        --object-lock-configuration \
        '{"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 13}}}'

    aws s3api get-object-lock-configuration --bucket acme-backups --endpoint-url "$ENDPOINT"
    aws s3api get-bucket-versioning --bucket acme-backups --endpoint-url "$ENDPOINT"   # "Enabled"
    ```

4. The settings:

    ```python
    'STORE': {
        'BACKEND': 'offsite_backup.stores.s3.S3Store',
        'ENDPOINT_URL': 'https://fsn1.your-objectstorage.com',
        'REGION': 'fsn1',
        'BUCKET': 'acme-backups',
        'ACCESS_KEY': os.environ.get('BACKUP_S3_ACCESS_KEY', ''),
        'SECRET_KEY': os.environ.get('BACKUP_S3_SECRET_KEY', ''),
        'LOCK_DAYS': 13,
    },
    ```

What was measured there, and what the package does about each — the reasoning is on
[Stores: S3](Stores-S3.md):

* a single PUT ignored the default rule and landed GOVERNANCE → the lock is an argument of
  every upload;
* a multipart upload took its date from the default rule → the rule must equal
  `LOCK_DAYS` (`E006`), and every lock is read back;
* a single PUT above 5 GB was refused → multipart in 64 MiB parts, volumes of 4 GiB;
* there is no IAM to withhold "bypass governance" → COMPLIANCE only;
* a delete without a version id only adds a marker → rotation deletes versions.

## AWS S3

Leave `ENDPOINT_URL` empty and set `REGION`.

```shell
aws s3api create-bucket --bucket acme-backups --region eu-central-1 \
    --create-bucket-configuration LocationConstraint=eu-central-1 \
    --object-lock-enabled-for-bucket

aws s3api put-object-lock-configuration --bucket acme-backups \
    --object-lock-configuration \
    '{"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 13}}}'
```

AWS can narrow a key to a prefix, so several projects may share a bucket safely. A
starting point for the backup user's policy — what a pass, the morning check,
`backup_list`, `keep_apart` and a restore call, and nothing that changes the bucket:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketVersioning", "s3:GetBucketObjectLockConfiguration"],
      "Resource": "arn:aws:s3:::acme-backups"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:ListBucketVersions"],
      "Resource": "arn:aws:s3:::acme-backups",
      "Condition": {"StringLike": {"s3:prefix": ["acme/prod/*"]}}
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject", "s3:PutObjectRetention", "s3:AbortMultipartUpload",
        "s3:GetObject", "s3:GetObjectVersion", "s3:GetObjectRetention",
        "s3:DeleteObjectVersion"
      ],
      "Resource": "arn:aws:s3:::acme-backups/acme/prod/*"
    }
  ]
}
```

Do **not** grant `s3:BypassGovernanceRetention`, `s3:PutBucketObjectLockConfiguration`,
`s3:PutBucketVersioning` or `s3:DeleteBucket`. A restore needs only the read actions, and
is better done with a separate read-only key from your own machine.

## MinIO

The package's integration tests run against MinIO with Object Lock and versioning.

```shell
mc alias set backups https://minio.example.com "$ACCESS_KEY" "$SECRET_KEY"
mc mb --with-lock backups/acme-backups
mc retention set --default COMPLIANCE 13d backups/acme-backups
mc retention info --default backups/acme-backups
```

```python
'STORE': {
    'BACKEND': 'offsite_backup.stores.s3.S3Store',
    'ENDPOINT_URL': 'https://minio.example.com',
    'REGION': 'us-east-1',
    'BUCKET': 'acme-backups',
    ...
},
```

A MinIO on the same machine as the application is not off-site. Run it elsewhere, or
treat it like a [directory store](Stores-directory.md).

## Cloudflare R2, Backblaze B2 and others

**Not yet verified.** This package needs, from the S3 API:

* `PutObject` and multipart uploads that accept `ObjectLockMode` and
  `ObjectLockRetainUntilDate`, and `HeadObject` that returns them;
* `GetObjectLockConfiguration` with a default retention rule, and `GetBucketVersioning`;
* `ListObjectVersions` with delete markers, and `DeleteObject` with a `VersionId`.

Check your provider's S3-compatibility documentation for each of these before relying on
it. Then prove it on a throwaway bucket: `manage.py check --deploy` tells you what the
bucket answers (`E005`, `E006`), and a pass with a media volume above 64 MiB tells you
whether a multipart object lands with the date asked (`BackupUnlockedError` if not).

A provider that cannot lock objects can still hold backups with `LOCK_DAYS: 0` — encrypted,
off the machine, and deletable by the key on the server. Say so in your own runbook; the
[Security model](Security-model.md) explains what that gives up.

Reports of what works on other providers are welcome as issues.
