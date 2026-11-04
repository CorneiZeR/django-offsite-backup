# Several stores

```python
'STORE': [
    {
        'NAME': 'offsite',
        'BACKEND': 'offsite_backup.stores.s3.S3Store',
        'ENDPOINT_URL': os.environ.get('BACKUP_S3_ENDPOINT', ''),
        'BUCKET': os.environ.get('BACKUP_S3_BUCKET', ''),
        'ACCESS_KEY': os.environ.get('BACKUP_S3_ACCESS_KEY', ''),
        'SECRET_KEY': os.environ.get('BACKUP_S3_SECRET_KEY', ''),
        'LOCK_DAYS': 13,
    },
    {
        'NAME': 'local',
        'BACKEND': 'offsite_backup.stores.filesystem.DirectoryStore',
        'ROOT': '/backups',
        'KEEP_NIGHTS': 3,
    },
],
```

`STORE` takes a list. The usual pair: a local volume for a fast restore of last night, and
an S3 bucket with Object Lock for the day the machine is gone — each kept as long as it is
worth keeping. A single block (no list) works exactly as before and needs no `NAME`.

## One pass, every store

Every object a pass writes goes to **each** store in turn, under the same key: the
databases, every media volume and its manifest, the configuration archive. Each store
reads its own lock back — the S3 copy is COMPLIANCE-locked and checked, the directory copy
has none — and each is [rotated](Retention.md) on its own, by its own `KEEP_NIGHTS`
(`RETENTION['KEEP_NIGHTS']` when it sets none). `E007` compares each store's window with
**its own** `LOCK_DAYS`.

The message and `backup_offsite` count each object once and name the stores:

```text
📦 Objects: 6
⚖️ Size: 412.7 MB
🗃 Stores: offsite, local
```

```text
backup_offsite: 6 objects, 432758912 bytes up to each of 2 stores
```

## A store that fails

**No silent partial.** A store that refuses an object — a full disk, an expired key, a
bucket out of reach — is recorded, written to no more in this pass, and **not rotated**:
its night is lost, and rotating it would make room for a backup it does not hold. The
other stores carry the pass to the end, and then the pass **fails by the store's name**:

```text
backup_offsite: 1 store(s) not written, the others hold the pass — local: OSError: [Errno 28] No space left on device
```

The FAILED message says the same, the last-success file is not written, and the nightly
task's retry runs the whole pass again. With every store refusing there is nothing to carry
on with, and the pass stops at the first object. With one store, a refusal is the pass's
own error, as it always was.

The morning check asks **every** store and names the folder with its store
(`local: config/ (none)`): the copy that stopped arriving is the one nobody looks at until
the day it is needed. The `database` guard counts a night as done only when every store
holds it.

## Names

With a list, every store needs a `NAME`, and no two the same — `E001` otherwise, and a
pass refuses to start. The name is how the message, the morning check and `--store` call
it:

```shell
python manage.py backup_list --store local
python manage.py backup_restore --stamp latest --store local --apply --database default
```

Reads — `backup_list`, `backup_restore`, `restore_database(..., store=)`, `fetch(...,
store=)`, the `latest` stamp — go to the **first** store unless one is named. Put the store
you restore from most often first; name the other when the first is out of reach.

`back_up_database()` writes its one-off dump to every store and raises if any refuses it —
a removal that cannot be backed up everywhere does not happen; `keep_apart()` copies in
every store.
