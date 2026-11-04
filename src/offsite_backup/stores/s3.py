"""An S3 bucket with Object Lock, as the store backups are kept in.

Facts measured against a real provider, each answered here:

* A single PUT ignores the bucket's default retention, and GOVERNANCE protects nothing
  (the writing key can bypass it). So every upload passes COMPLIANCE and a date.
* A multipart upload keeps the mode but takes its date from the bucket's default rule,
  not the request — so the bucket must have a default COMPLIANCE rule of ``LOCK_DAYS``
  days (E006), and every object's lock is read back after upload.
* One PUT stops at 5 GB: uploads go multipart above 64 MiB parts.
* On a versioned bucket a delete without a version id only writes a delete marker, so
  rotation lists and deletes *versions*.

boto3 is imported when a client is first built, so this module imports without it.
"""

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from offsite_backup import findings as checks
from offsite_backup.exceptions import BackupUnlockedError
from offsite_backup.stores.base import Store, Version, Wrote

__all__ = (
    'ADDRESSING_STYLES',
    'CLIENT_FACTORY',
    'PART_BYTES',
    'S3Store',
    'check_client_options',
    'make_client',
    'transfer_config',
)

ADDRESSING_STYLES = ('auto', 'path', 'virtual')

#: 64 MiB parts: a 4 GiB volume is 64 parts, and a failed part costs a minute
PART_BYTES = 64 * 1024 * 1024
#: how far a read-back lock may fall short: the bucket counts from completion, by its clock
CLOCK_SLACK = timedelta(hours=1)

#: a replacement for ``make_client``, set by ``offsite_backup.testing`` to a fake
CLIENT_FACTORY: Callable[[Mapping[str, Any]], Any] | None = None


def make_client(options: Mapping[str, Any]) -> Any:
    """An S3 client from these options and nothing else.

    Not the default credential chain: a stray ``~/.aws``, an instance role or an
    ``AWS_*`` variable would send backups somewhere nobody chose, reporting success.
    """
    if CLIENT_FACTORY is not None:
        return CLIENT_FACTORY(options)
    import boto3  # noqa: PLC0415 - the [s3] extra, imported when a client is built
    from botocore.config import Config  # noqa: PLC0415

    verify = options.get('VERIFY')
    return boto3.client(
        's3',
        endpoint_url=options.get('ENDPOINT_URL') or None,
        region_name=options.get('REGION') or None,
        aws_access_key_id=options.get('ACCESS_KEY') or None,
        aws_secret_access_key=options.get('SECRET_KEY') or None,
        # True, False, or a CA bundle's path for a store behind an internal certificate
        verify=True if verify is None or verify == '' else verify,
        config=Config(
            retries={'max_attempts': 5, 'mode': 'standard'},
            s3={'addressing_style': options.get('ADDRESSING_STYLE') or 'auto'},
            signature_version=options.get('SIGNATURE_VERSION') or None,
        ),
    )


def check_client_options(options: Mapping[str, Any], label: str) -> list[checks.CheckMessage]:
    """``ADDRESSING_STYLE`` is one botocore knows; ``VERIFY`` a bool or an existing CA file."""
    found: list[checks.CheckMessage] = []
    style = options.get('ADDRESSING_STYLE') or 'auto'
    if style not in ADDRESSING_STYLES:
        found.append(
            checks.Error(
                f"{label}['ADDRESSING_STYLE'] is {style!r}; expected one of {', '.join(ADDRESSING_STYLES)}.",
                hint='MinIO and SeaweedFS need "path"; AWS works with "virtual".',
                id='offsite_backup.E001',
            )
        )
    verify = options.get('VERIFY')
    if isinstance(verify, str) and verify and not Path(verify).is_file():
        found.append(checks.Error(f"{label}['VERIFY'] is {verify!r}, which is not a file.", id='offsite_backup.E001'))
    return found


def transfer_config() -> Any:
    """Multipart above one part, in 64 MiB parts."""
    if CLIENT_FACTORY is not None:
        return None
    from boto3.s3.transfer import TransferConfig  # noqa: PLC0415 - the [s3] extra

    return TransferConfig(multipart_threshold=PART_BYTES, multipart_chunksize=PART_BYTES)


def _code(error: Exception) -> str:
    """The S3 error code of a botocore ``ClientError`` (or a fake's), else ``''``."""
    response = getattr(error, 'response', None)
    if isinstance(response, Mapping):
        return str(response.get('Error', {}).get('Code', ''))
    return ''


class S3Store(Store):
    """``BUCKET`` at ``ENDPOINT_URL``, every object locked COMPLIANCE for ``LOCK_DAYS`` days."""

    REQUIRED = ('BUCKET', 'ACCESS_KEY', 'SECRET_KEY')
    SIGNALS = ('BUCKET', 'ACCESS_KEY', 'SECRET_KEY', 'ENDPOINT_URL')
    REACHES_NETWORK = True
    LOCKS = True

    _client: Any = None

    @property
    def client(self) -> Any:
        """Built on first use, so constructing the store touches nothing."""
        if self._client is None:
            self._client = make_client(self.options)
        return self._client

    @property
    def bucket(self) -> str:
        """The bucket name."""
        return str(self.option('BUCKET'))

    @property
    def lock_days(self) -> int:
        """Days every object is locked; 0 turns locking off (a bucket without Object Lock)."""
        return int(self.option('LOCK_DAYS', 13))

    def _lock_args(self, now: datetime | None) -> tuple[dict[str, Any], datetime | None]:
        if not self.lock_days:
            return {}, None
        until = (now or datetime.now(timezone.utc)) + timedelta(days=self.lock_days)
        return {'ObjectLockMode': 'COMPLIANCE', 'ObjectLockRetainUntilDate': until}, until

    def _read_back(self, key: str, until: datetime | None) -> datetime | None:
        """Raise unless the object landed COMPLIANCE until at least ``until`` (less the slack)."""
        landed = self.client.head_object(Bucket=self.bucket, Key=key)
        if until is None:
            return None
        mode = landed.get('ObjectLockMode')
        kept = landed.get('ObjectLockRetainUntilDate')
        if mode != 'COMPLIANCE' or kept is None or kept < until - CLOCK_SLACK:
            msg = (
                f'{key} landed {mode or "unlocked"} until {kept}; asked COMPLIANCE until {until:%Y-%m-%d %H:%M}Z. '
                'A short date on a large object means the bucket has no default COMPLIANCE rule of '
                f'{self.lock_days} days (multipart uploads take their date from it).'
            )
            raise BackupUnlockedError(msg)
        return kept  # type: ignore[no-any-return]

    def put(self, path: Path, key: str, *, now: datetime | None = None) -> Wrote:
        """Upload with the lock as arguments, then read the lock back."""
        extra, until = self._lock_args(now)
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra, Config=transfer_config())
        kept = self._read_back(key, until)
        return Wrote(key=key, size=path.stat().st_size, locked_until=kept)

    def copy(self, source: str, key: str, *, now: datetime | None = None) -> Wrote:
        """The managed copy (``copy_object`` stops at 5 GB, as a PUT does), locked afresh."""
        extra, until = self._lock_args(now)
        self.client.copy(
            {'Bucket': self.bucket, 'Key': source}, self.bucket, key, ExtraArgs=extra, Config=transfer_config()
        )
        kept = self._read_back(key, until)
        size = int(self.client.head_object(Bucket=self.bucket, Key=key).get('ContentLength', 0))
        return Wrote(key=key, size=size, locked_until=kept)

    def versions(self, prefix: str) -> dict[str, list[Version]]:
        """Every version and delete marker under ``prefix``, across every page."""
        found: dict[str, list[Version]] = {}
        markers: dict[str, str] = {}
        while True:
            page = self.client.list_object_versions(Bucket=self.bucket, Prefix=prefix, **markers)
            for item in page.get('Versions') or ():
                found.setdefault(str(item['Key']), []).append(
                    Version(str(item['VersionId']), latest=bool(item.get('IsLatest')), size=int(item.get('Size', 0)))
                )
            for item in page.get('DeleteMarkers') or ():
                found.setdefault(str(item['Key']), []).append(
                    Version(str(item['VersionId']), marker=True, latest=bool(item.get('IsLatest')))
                )
            if not page.get('IsTruncated'):
                return found
            markers = {'KeyMarker': page['NextKeyMarker'], 'VersionIdMarker': page.get('NextVersionIdMarker', '')}

    def current(self, prefix: str) -> list[str]:
        """A plain listing: only current objects, cheaper than every version."""
        keys: list[str] = []
        token: dict[str, str] = {}
        while True:
            page = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix, **token)
            keys.extend(str(item['Key']) for item in page.get('Contents') or ())
            if not page.get('IsTruncated'):
                return sorted(keys)
            token = {'ContinuationToken': page['NextContinuationToken']}

    def delete(self, key: str, version: Version) -> None:
        """Delete one version by id — never a bare key, which would only add a marker."""
        self.client.delete_object(Bucket=self.bucket, Key=key, VersionId=version.id)

    def locked_until(self, key: str, version: Version) -> datetime | None:
        """The version's retain-until date; a delete marker has none."""
        if version.marker:
            return None
        head = self.client.head_object(Bucket=self.bucket, Key=key, VersionId=version.id)
        return head.get('ObjectLockRetainUntilDate')  # type: ignore[no-any-return]

    def get(self, key: str, into: Path) -> None:
        """Download the current version."""
        self.client.download_file(self.bucket, key, str(into), Config=transfer_config())

    def check(self) -> list[checks.CheckMessage]:
        """E007: retention must outlast the lock, or rotation meets locked objects every night."""
        found = [*super().check(), *check_client_options(self.options, self.label)]
        from offsite_backup.conf import read  # noqa: PLC0415 - avoids a cycle at import

        keep = read()['RETENTION'].get('KEEP_NIGHTS')
        if self.lock_days and isinstance(keep, int) and keep <= self.lock_days:
            found.append(
                checks.Error(
                    f"RETENTION['KEEP_NIGHTS'] is {keep}, not more than {self.label}['LOCK_DAYS'] = {self.lock_days}.",
                    hint='Keep at least one night more than the lock (e.g. 14 nights, 13 days), '
                    'so rotation only ever deletes objects whose lock has run out.',
                    id='offsite_backup.E007',
                )
            )
        return found

    def deploy_check(self) -> list[checks.CheckMessage]:
        """E005/E006: the bucket is reachable, versioned, locked, with a default rule of LOCK_DAYS."""
        try:
            versioning = self.client.get_bucket_versioning(Bucket=self.bucket)
        except Exception as error:  # noqa: BLE001 - any failure here is the finding
            return [
                checks.Error(
                    f'{self.label}: bucket {self.bucket!r} could not be read ({_code(error) or type(error).__name__}).',
                    hint='Check ENDPOINT_URL, the bucket name and the key.',
                    id='offsite_backup.E005',
                )
            ]
        if not self.lock_days:
            return []
        found: list[checks.CheckMessage] = []
        if versioning.get('Status') != 'Enabled':
            found.append(
                checks.Error(
                    f'{self.label}: bucket {self.bucket!r} is not versioned.',
                    hint='Object Lock needs versioning; both are only possible on a bucket created with Object Lock.',
                    id='offsite_backup.E005',
                )
            )
        try:
            lock = self.client.get_object_lock_configuration(Bucket=self.bucket).get('ObjectLockConfiguration', {})
        except Exception as error:  # noqa: BLE001 - "no configuration" arrives as an error
            lock = {}
            if _code(error) not in {'ObjectLockConfigurationNotFoundError', 'ObjectLockConfigurationNotFound'}:
                found.append(
                    checks.Error(
                        f'{self.label}: the Object Lock configuration of {self.bucket!r} could not be read '
                        f'({_code(error) or type(error).__name__}).',
                        id='offsite_backup.E005',
                    )
                )
                return found
        if lock.get('ObjectLockEnabled') != 'Enabled':
            found.append(
                checks.Error(
                    f'{self.label}: bucket {self.bucket!r} has no Object Lock.',
                    hint='Object Lock can only be enabled when a bucket is created: create a new bucket with it, '
                    'or set LOCK_DAYS to 0 to store without immutability.',
                    id='offsite_backup.E005',
                )
            )
            return found
        rule = lock.get('Rule', {}).get('DefaultRetention', {})
        if rule.get('Mode') != 'COMPLIANCE' or rule.get('Days') != self.lock_days:
            found.append(
                checks.Error(
                    f'{self.label}: bucket {self.bucket!r} has default retention {rule or "none"}, '
                    f'not COMPLIANCE {self.lock_days} days.',
                    hint='Multipart uploads (anything over 64 MiB) take their lock date from this rule, not from '
                    f'the request; set a default rule of COMPLIANCE, {self.lock_days} days.',
                    id='offsite_backup.E006',
                )
            )
        return found
