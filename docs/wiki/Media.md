# Media

```python
'MEDIA': [
    {'BACKEND': 'offsite_backup.media.filesystem.DirectorySource'},
],
```

Uploaded files, whatever holds them, packed into **volumes** of whole files.

## Where the media lives

| media lives… | backend | |
| --- | --- | --- |
| a directory on disk | `offsite_backup.media.filesystem.DirectorySource` | `ROOT`, default `settings.MEDIA_ROOT` |
| a Docker volume mounted into the container that runs the backup | `offsite_backup.media.filesystem.DirectorySource` | inside the container a volume is a directory |
| an S3 bucket (any S3-compatible server) | `offsite_backup.media.s3.S3Source` | `[s3]` extra; `BUCKET`, `PREFIX` |
| nowhere | leave `MEDIA` out | |

=== "Directory"

    ```python
    'MEDIA': [
        {
            'BACKEND': 'offsite_backup.media.filesystem.DirectorySource',
            'ROOT': '/app/media',
            'EXCLUDE': ['cache/*', '*/thumbs/*'],
        },
    ],
    ```

    Regular files under `ROOT`, in sorted order; names inside the tar are relative to
    `ROOT`. Symbolic links are not followed. A file that cannot be read is logged
    (`Media file not readable; left out`) and left out rather than failing the night —
    unless `STRICT` is on, and then it fails the pass, naming the file.
    A `ROOT` that does not exist is `W005` — a missing mount is worth knowing at start,
    not at 03:00.

=== "S3 bucket"

    ```python
    'MEDIA': [
        {
            'BACKEND': 'offsite_backup.media.s3.S3Source',
            'ENDPOINT_URL': os.environ.get('MEDIA_S3_ENDPOINT', ''),
            'BUCKET': os.environ.get('MEDIA_S3_BUCKET', ''),
            'PREFIX': 'private/media/',
            'ACCESS_KEY': os.environ.get('MEDIA_S3_ACCESS_KEY', ''),
            'SECRET_KEY': os.environ.get('MEDIA_S3_SECRET_KEY', ''),
        },
    ],
    ```

    Every object under `PREFIX`, in key order, streamed straight from the bucket into the
    tar — nothing is staged on disk but the sealed volume. The object's full key is its
    name inside the archive. The source has its own credentials: a read-only key for the
    media bucket, not the backup store's. `ADDRESSING_STYLE`, `SIGNATURE_VERSION` and
    `VERIFY` work as on the store — a MinIO or SeaweedFS media bucket needs
    `'ADDRESSING_STYLE': 'path'` ([S3 providers](S3-providers.md#addressing-style)).

## Volumes

Media goes up in volumes of at most `VOLUME_BYTES` (4 GiB by default):

```text
acme/prod/media/2026-03-10T03-00Z.001.tar.age
acme/prod/media/2026-03-10T03-00Z.002.tar.age
acme/prod/media/2026-03-10T03-00Z.003.tar.age
```

* **Whole files only.** Each volume is a complete tar on its own: losing one loses its
  files, not the night, and any one of them restores with `tar -x`.
* **A file larger than a volume** gets a volume of its own.
* **The budget counts tar's overhead**: 20 KiB per volume (the end-of-archive blocks and
  the padding to a record) and 2 KiB per file (a 512-byte header, padding to 512, and a PAX
  header for a long or non-ASCII name), so a volume of many small files stays under the limit.
* **Streamed, one at a time**: source → tar → `age` → disk → upload → delete, then the next
  volume. The workspace needs room for one sealed volume, not for the media; media larger
  than the free disk works.
* **Why 4 GiB**: one S3 PUT stops at 5 GB. Volumes above 64 MiB go up multipart anyway, but
  a volume small enough for one PUT is the one that never surprises anyone. `VOLUME_BYTES`
  must be more than 20 KiB and at most 5 GB (`E001`).

Volumes are written with Python's `tarfile` in PAX format — measured faster than the `tar`
CLI (0.27 s against 0.72 s on 613 MB in 2400 files), and the only option when the files
come from S3. Each member is written with mode `0644` and the file's modification time; the
size is taken when the file is opened, so a file that changed since it was listed is
archived as it is now.

**Not compressed by default.** Images, video and PDFs are already compressed; zstd would
spend the night gaining nothing. `COMPRESS: True` on a source runs the global `COMPRESS`
stage on its volumes as well, and the suffix becomes `.tar.zst.age`.

## Filters

`INCLUDE` and `EXCLUDE` are lists of shell globs matched against each file's name inside
the archive (`fnmatch`: `*` matches across `/` too); blank items are dropped. With
`INCLUDE`, only matching names are kept; then anything matching `EXCLUDE` is left out:

```python
'EXCLUDE': ['*.tmp', 'cache/*', '*/thumbs/*'],   # regenerable, not worth a night's upload
```

For `S3Source` the name includes `PREFIX` — write `'private/media/cache/*'`, or narrow
`PREFIX` instead.

## Several sources

`MEDIA` is a list. Each source is a kind of its own, with its own volumes and its own
rotation; give each a `NAME`:

```python
'MEDIA': [
    {'BACKEND': 'offsite_backup.media.filesystem.DirectorySource', 'ROOT': '/app/media'},
    {'BACKEND': 'offsite_backup.media.filesystem.DirectorySource', 'ROOT': '/app/private', 'NAME': 'private'},
],
```

```text
acme/prod/media/...
acme/prod/media-private/...
```

A single block instead of a list is accepted too.

## Empty is not an error — unless it is required

A source with no files writes no volume, and the message says how many objects went up.
The [morning check](Monitoring.md) does not depend on media: tonight's media does not
stand in for a database that was not written.

When an empty source can only mean a missing mount or a wrong `ROOT` / `PREFIX`, make it
required: `'REQUIRED': True` on the source (or `STRICT: True` for the whole pass) turns "no
files at all" into a failed pass, with `SourceMissingError` naming the source in the FAILED
message ([Settings](Settings.md#strict-and-required)).

## Leaving media out of one pass

`backup_offsite --without-media` (or `--only db --only config`) writes the databases and
the configuration only, and — in a project that has `MEDIA` configured — marks every
object `.without-media.`: the pass a deploy runs. Those objects never count as a night; see [Retention](Retention.md) and
[Deploy integration](Deploy-integration.md).

## Not yet

A Docker volume *not* mounted where the backup runs (through the Docker API), any Django
storage backend, and a manifest per volume for restoring one folder without downloading
everything are on the roadmap.
