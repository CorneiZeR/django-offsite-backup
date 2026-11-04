"""Media in an S3 bucket (any S3-compatible server), streamed object by object into the tar."""

import contextlib
from collections.abc import Iterator
from typing import IO, Any

from offsite_backup import findings as checks
from offsite_backup.media.base import MediaEntry, MediaSource
from offsite_backup.stores.s3 import check_client_options, make_client

__all__ = ('S3Source',)


class S3Source(MediaSource):
    """``BUCKET`` (``PREFIX``) at ``ENDPOINT_URL``; the object key is the path in the archive."""

    REQUIRED = ('BUCKET',)
    REACHES_NETWORK = True

    _client: Any = None

    @property
    def client(self) -> Any:
        """Built on first use."""
        if self._client is None:
            self._client = make_client(self.options)
        return self._client

    def entries(self) -> Iterator[MediaEntry]:
        """Every object under ``PREFIX``, in key order, across every page."""
        token: dict[str, str] = {}
        prefix = str(self.option('PREFIX', ''))
        while True:
            page = self.client.list_objects_v2(Bucket=self.option('BUCKET'), Prefix=prefix, **token)
            for item in page.get('Contents') or ():
                key = str(item['Key'])
                if key.endswith('/'):
                    continue
                modified = item.get('LastModified')
                yield MediaEntry(key, int(item['Size']), modified.timestamp() if modified else 0.0)
            if not page.get('IsTruncated'):
                return
            token = {'ContinuationToken': page['NextContinuationToken']}

    @contextlib.contextmanager
    def open(self, entry: MediaEntry) -> Iterator[tuple[IO[bytes], int]]:
        """The object's body, streamed; its length as the server says now."""
        found = self.client.get_object(Bucket=self.option('BUCKET'), Key=entry.name)
        body = found['Body']
        try:
            yield body, int(found['ContentLength'])
        finally:
            body.close()

    def check(self) -> list[checks.CheckMessage]:
        """The client options, as the store checks them."""
        return [*super().check(), *check_client_options(self.options, self.label)]
