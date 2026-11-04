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
| a Docker volume **not** mounted there | `offsite_backup.media.docker_volume.VolumeSource` | `VOLUME`; the `docker` CLI and the daemon's socket |
| an S3 bucket (any S3-compatible server) | `offsite_backup.media.s3.S3Source` | `[s3]` extra; `BUCKET`, `PREFIX` |
| any Django storage (django-storages' S3, GCS, Azure, …) | `offsite_backup.media.django_storage.StorageSource` | `STORAGE` (an alias of `STORAGES`), `PREFIX` |
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
    A `ROOT` that does not exist is `W005` under `manage.py check --deploy` — a missing
    mount is worth knowing at start, not at 03:00 — and the pass logs
    `Media directory not found`. A plain `manage.py check` does not warn: the web service
    and a `migrate` container need no media mount
    ([why](System-checks.md#why-the-path-checks-are-deploy-checks)).

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

=== "Docker volume, not mounted"

    ```python
    'MEDIA': [
        {
            'BACKEND': 'offsite_backup.media.docker_volume.VolumeSource',
            'VOLUME': 'acme_media',          # as `docker volume ls` shows it
        },
    ],
    ```

    For a worker that does not mount the volume. A throwaway container is **created, never
    started** — read-only mount, no network, label `offsite_backup=media` — and
    `docker cp <container>:/volume/. -` streams the volume through the daemon as one tar.
    The source cuts that one stream into volumes as it goes, so the Docker volume is read
    once and nothing but the sealed volume being written touches the disk; the container is
    removed afterwards, whatever happened. Nothing in the image runs, so any small image
    will do (`IMAGE`, default `busybox:stable`, pulled by `docker create` if absent).

    Regular files only: symbolic links are not followed, and a hard link — which one stream
    read once cannot carry — is logged (`Media file is a hard link; left out`) and left out,
    or fails the pass under `STRICT`. The volume is asked about first: `docker create` would
    make a missing named volume, empty, and a typo would then back up nothing every night.
    A missing volume is logged (`Media volume not found`), fails a `REQUIRED` source, and is
    `W005` (`E009` when required) under `check --deploy`; a daemon that cannot be asked is
    `E012`.

    The worker needs the `docker` CLI and the daemon's socket (`DOCKER_HOST` is honoured).
    **Access to that socket is root on the host** ([Security model](Security-model.md)):
    mount the volume read-only into the worker and use `DirectorySource` wherever you can.

=== "Django storage"

    ```python
    'MEDIA': [
        {
            'BACKEND': 'offsite_backup.media.django_storage.StorageSource',
            'STORAGE': 'default',            # an alias of settings.STORAGES
            'PREFIX': 'uploads/',
        },
    ],
    ```

    Whatever backend the project stores files with, through Django's own API: `listdir`
    from `PREFIX` down (files before folders at each level, sorted), `size`, and `open`,
    read a block at a time — **no file is read whole**. Names in the archive are the
    storage's names. A file deleted between listing and reading is logged and left out.

    django-storages' S3 file is read from its object's body directly
    (`file.obj.get()['Body']`, streamed): its own `read()` first downloads the whole object
    into a `SpooledTemporaryFile`, which stays in memory unless `AWS_S3_MAX_MEMORY_SIZE` is
    set. Other cloud backends spool a file the same way before handing it over — set their
    own max-memory setting (`GS_MAX_MEMORY_SIZE` for GCS) so large files spool to disk.
    For an S3 bucket, `S3Source` is the faster choice anyway: it lists in one pass where a
    storage asks for each file's size. A `STORAGE` that `STORAGES` does not define is `E001`;
    a `PREFIX` that cannot be listed under `check --deploy` is `E012`.

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
  volume — packed while the files stream, so a source is listed (or, for a Docker volume,
  read) once. The workspace needs room for one sealed volume, not for the media; media larger
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

## Manifests

Every volume has a manifest beside it, sealed like the volume (not compressed):

```text
acme/prod/media/2026-03-10T03-00Z.001.tar.age
acme/prod/media/2026-03-10T03-00Z.001.manifest.json.age
```

```json
{"format": 1, "volume": "2026-03-10T03-00Z.001.tar.age",
 "files": [{"path": "invoices/2026/03/0001.pdf", "size": 48213, "sha256": "9f2c…"}]}
```

Each file's path, size and SHA-256 are taken **while it streams into the tar** — the bytes
written, not the size listed. A restore of one file or one folder
(`backup_restore --path`, [Restore](Restore.md#some-media-files)) downloads the manifests,
then only the volumes that hold a match, and checks every file against its hash. A manifest
goes up right after its volume, rotates with it (its night is in its name), and is counted
among the pass's objects. File names can be as telling as the files, so a manifest is
encrypted to the same recipients.

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

