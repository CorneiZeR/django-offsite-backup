"""An in-memory S3 that behaves like the providers backups are kept at — including where they bite.

Versions and delete markers, Object Lock (a locked version refuses deletion), pagination
(``page_size`` items per page, so loops over pages are exercised), and the measured quirks:

* a single PUT without lock arguments lands GOVERNANCE, whatever the bucket's default;
* a multipart upload (``>= multipart_threshold`` bytes) keeps the requested mode but takes
  its retain-until date from the bucket's default rule — none without one;
* a single PUT above ``put_limit`` is refused with ``EntityTooLarge``.
"""

import io
import itertools
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

__all__ = ('FakeBucket', 'FakeClientError', 'FakeS3', 'FakeVersion')


class FakeClientError(Exception):
    """Shaped like botocore's ``ClientError``: ``.response['Error']['Code']``."""

    def __init__(self, code: str, message: str = '') -> None:
        """Keep the code where botocore keeps it."""
        super().__init__(f'{code}: {message}' if message else code)
        self.response = {'Error': {'Code': code, 'Message': message}}


@dataclass
class FakeVersion:
    """One stored version."""

    id: str
    data: bytes | None
    modified: datetime
    mode: str | None = None
    until: datetime | None = None

    @property
    def marker(self) -> bool:
        """A delete marker holds no data."""
        return self.data is None


@dataclass
class FakeBucket:
    """A bucket: lock settings and every key's versions, oldest first."""

    versioned: bool = True
    lock_enabled: bool = True
    default_mode: str | None = 'COMPLIANCE'
    default_days: int | None = 13
    keys: dict[str, list[FakeVersion]] = field(default_factory=dict)


class FakeS3:
    """The subset of a boto3 S3 client this package uses, over in-memory buckets."""

    def __init__(
        self,
        *,
        page_size: int = 3,
        multipart_threshold: int = 64 * 1024 * 1024,
        put_limit: int = 5 * 10**9,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Start with no buckets; ``bucket()`` creates one."""
        self.buckets: dict[str, FakeBucket] = {}
        self.page_size = page_size
        self.multipart_threshold = multipart_threshold
        self.put_limit = put_limit
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._ids = itertools.count(1)

    def bucket(self, name: str, **settings: Any) -> FakeBucket:
        """Create (or reconfigure) a bucket."""
        found = self.buckets.setdefault(name, FakeBucket())
        for key, value in settings.items():
            setattr(found, key, value)
        return found

    def _bucket(self, name: str) -> FakeBucket:
        if name not in self.buckets:
            raise FakeClientError('NoSuchBucket', name)
        return self.buckets[name]

    def _current(self, bucket: str, key: str, version_id: str | None = None) -> FakeVersion:
        held = self._bucket(bucket).keys.get(key, [])
        if version_id is not None:
            for version in held:
                if version.id == version_id:
                    return version
            raise FakeClientError('NoSuchVersion', key)
        if not held or held[-1].marker:
            raise FakeClientError('NoSuchKey', key)
        return held[-1]

    def _store(self, bucket: str, key: str, data: bytes, extra: Mapping[str, Any]) -> FakeVersion:
        found = self._bucket(bucket)
        now = self.clock()
        mode = extra.get('ObjectLockMode')
        until = extra.get('ObjectLockRetainUntilDate')
        multipart = len(data) >= self.multipart_threshold
        if not multipart and len(data) > self.put_limit:
            raise FakeClientError('EntityTooLarge', key)
        if found.lock_enabled:
            if multipart and mode:
                until = now + timedelta(days=found.default_days) if found.default_days else None
            elif not mode and found.default_mode:
                mode = 'GOVERNANCE' if not multipart else found.default_mode
                until = now + timedelta(days=found.default_days or 0)
        else:
            if mode:
                raise FakeClientError('InvalidRequest', 'Bucket is missing Object Lock Configuration')
            mode = until = None
        version = FakeVersion(str(next(self._ids)) if found.versioned else 'null', data, now, mode, until)
        held = found.keys.setdefault(key, [])
        if not found.versioned:
            held.clear()
        held.append(version)
        return version

    # -- the client surface -------------------------------------------------------------

    def put_object(self, Bucket: str, Key: str, Body: bytes = b'', **extra: Any) -> dict[str, Any]:  # noqa: N803
        """Store bytes (used to seed a media bucket)."""
        self.calls.append(('put_object', {'Key': Key}))
        version = self._store(Bucket, Key, bytes(Body), extra)
        return {'VersionId': version.id}

    def upload_file(
        self,
        Filename: str,  # noqa: N803
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        ExtraArgs: Mapping[str, Any] | None = None,  # noqa: N803
        Config: Any = None,  # noqa: N803
    ) -> None:
        """Store a file's bytes."""
        self.calls.append(('upload_file', {'Key': Key, 'ExtraArgs': dict(ExtraArgs or {})}))
        self._store(Bucket, Key, Path(Filename).read_bytes(), ExtraArgs or {})

    def copy(
        self,
        CopySource: Mapping[str, str],  # noqa: N803
        Bucket: str,  # noqa: N803
        Key: str,  # noqa: N803
        ExtraArgs: Mapping[str, Any] | None = None,  # noqa: N803
        Config: Any = None,  # noqa: N803
    ) -> None:
        """Copy the current version of one key to another."""
        source = self._current(CopySource['Bucket'], CopySource['Key'])
        self.calls.append(('copy', {'Key': Key}))
        self._store(Bucket, Key, source.data or b'', ExtraArgs or {})

    def head_object(self, Bucket: str, Key: str, VersionId: str | None = None) -> dict[str, Any]:  # noqa: N803
        """A version's size and lock."""
        version = self._current(Bucket, Key, VersionId)
        if version.marker:
            raise FakeClientError('MethodNotAllowed', Key)
        head: dict[str, Any] = {'ContentLength': len(version.data or b''), 'LastModified': version.modified}
        if version.mode:
            head['ObjectLockMode'] = version.mode
        if version.until:
            head['ObjectLockRetainUntilDate'] = version.until
        return head

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        """The current version's body."""
        version = self._current(Bucket, Key)
        data = version.data or b''
        return {'Body': io.BytesIO(data), 'ContentLength': len(data), 'LastModified': version.modified}

    def download_file(self, Bucket: str, Key: str, Filename: str, Config: Any = None) -> None:  # noqa: N803
        """Write the current version to a file."""
        with Path(Filename).open('wb') as sink:
            shutil.copyfileobj(io.BytesIO(self._current(Bucket, Key).data or b''), sink)

    def delete_object(self, Bucket: str, Key: str, VersionId: str | None = None) -> dict[str, Any]:  # noqa: N803
        """With a version id, remove that version (refused while locked); without, add a delete marker."""
        found = self._bucket(Bucket)
        self.calls.append(('delete_object', {'Key': Key, 'VersionId': VersionId}))
        held = found.keys.get(Key, [])
        if VersionId is None:
            if found.versioned:
                held.append(FakeVersion(str(next(self._ids)), None, self.clock()))
                found.keys[Key] = held
            else:
                found.keys.pop(Key, None)
            return {}
        version = self._current(Bucket, Key, VersionId)
        if version.mode and version.until and version.until > self.clock():
            raise FakeClientError('AccessDenied', f'{Key} is locked until {version.until}')
        held.remove(version)
        if not held:
            found.keys.pop(Key, None)
        return {}

    def list_object_versions(
        self,
        Bucket: str,  # noqa: N803
        Prefix: str = '',  # noqa: N803
        KeyMarker: str = '',  # noqa: N803
        VersionIdMarker: str = '',  # noqa: N803
    ) -> dict[str, Any]:
        """Every version and marker, newest first per key, ``page_size`` per page."""
        rows: list[tuple[str, FakeVersion, bool]] = []
        for key in sorted(self._bucket(Bucket).keys):
            if not key.startswith(Prefix):
                continue
            held = self._bucket(Bucket).keys[key]
            rows += [(key, version, index == 0) for index, version in enumerate(reversed(held))]
        start = 0
        if KeyMarker:
            start = next(
                index + 1
                for index, (key, version, _) in enumerate(rows)
                if (key, version.id) == (KeyMarker, VersionIdMarker)
            )
        page = rows[start : start + self.page_size]
        answer: dict[str, Any] = {
            'Versions': [],
            'DeleteMarkers': [],
            'IsTruncated': start + self.page_size < len(rows),
        }
        for key, version, latest in page:
            item = {'Key': key, 'VersionId': version.id, 'IsLatest': latest, 'LastModified': version.modified}
            if version.marker:
                answer['DeleteMarkers'].append(item)
            else:
                answer['Versions'].append({**item, 'Size': len(version.data or b'')})
        if answer['IsTruncated']:
            answer['NextKeyMarker'], answer['NextVersionIdMarker'] = page[-1][0], page[-1][1].id
        return answer

    def list_objects_v2(self, Bucket: str, Prefix: str = '', ContinuationToken: str = '') -> dict[str, Any]:  # noqa: N803
        """Current objects only, ``page_size`` per page."""
        rows = [
            (key, held[-1])
            for key, held in sorted(self._bucket(Bucket).keys.items())
            if key.startswith(Prefix) and held and not held[-1].marker
        ]
        start = int(ContinuationToken or 0)
        page = rows[start : start + self.page_size]
        truncated = start + self.page_size < len(rows)
        answer: dict[str, Any] = {
            'Contents': [
                {'Key': key, 'Size': len(version.data or b''), 'LastModified': version.modified}
                for key, version in page
            ],
            'IsTruncated': truncated,
        }
        if truncated:
            answer['NextContinuationToken'] = str(start + self.page_size)
        return answer

    def get_bucket_versioning(self, Bucket: str) -> dict[str, Any]:  # noqa: N803
        """``Enabled`` or nothing."""
        return {'Status': 'Enabled'} if self._bucket(Bucket).versioned else {}

    def get_object_lock_configuration(self, Bucket: str) -> dict[str, Any]:  # noqa: N803
        """The lock configuration, or the error a bucket without one answers with."""
        found = self._bucket(Bucket)
        if not found.lock_enabled:
            raise FakeClientError('ObjectLockConfigurationNotFoundError', Bucket)
        config: dict[str, Any] = {'ObjectLockEnabled': 'Enabled'}
        if found.default_mode and found.default_days:
            config['Rule'] = {'DefaultRetention': {'Mode': found.default_mode, 'Days': found.default_days}}
        return {'ObjectLockConfiguration': config}
