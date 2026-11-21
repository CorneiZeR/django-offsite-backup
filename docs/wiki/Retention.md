# Retention and locks

```python
'RETENTION': {'KEEP_NIGHTS': 14},
'STORE': {..., 'LOCK_DAYS': 13},
```

Each kind keeps its newest **14 whole nights**; every object is locked for **13 days**.
One less, on purpose. Older backups can be kept too, one a week and one a month —
[below](#weekly-and-monthly-backups); both are off by default.

## Counted in whole nights

Retention counts **calendar nights in UTC, read from the object's name** — not objects,
and not age:

* not objects, because a night's media is several volumes, and a night with ten volumes
  would otherwise push out nine nights of databases;
* not stamps, because a project runs several passes a day — the nightly one, one per
  deploy, one by hand — and counting each would push out nights whose lock still holds;
* not the store's timestamps, because the name is what a person reads in a listing and
  what every store keeps the same way.

`2026-03-10T03-00Z` and `2026-03-10T14-12Z` are one night: 2026-03-10. A key whose name
carries no stamp is not a backup and is never touched.

Each kind folder rotates on its own — `db/`, `db-analytics/`, `media/`, `config/` — and
strictly inside `<PREFIX>/<kind>/`, **with the trailing slash**: `db/` never matches
`db-analytics/`, and `acme/prod/` never matches `acme/prod-old/`. A pass rotates the kinds
it wrote to, so a nightly pass rotates media and a deploy pass does not.

## Weekly and monthly backups

```python
'RETENTION': {'KEEP_NIGHTS': 7, 'KEEP_WEEKS': 4, 'KEEP_MONTHS': 3},
```

Past the daily window, rotation can keep one night a week and, older still, one night a
month. Both counts default to `0`, which keeps the daily window only. The tiers are chosen
among the same whole nights, read from the same names:

1. **daily** — the newest `KEEP_NIGHTS` nights, as above;
2. **weekly** — among the nights older than the oldest daily one, grouped by ISO week
   (Monday to Sunday): the **earliest** night present in each of the newest `KEEP_WEEKS`
   weeks;
3. **monthly** — among the nights older than the oldest weekly night kept (older than the
   oldest daily one when the weekly tier keeps none), grouped by calendar month: the
   **earliest** night present in each of the newest `KEEP_MONTHS` months.

Every other whole night is deleted. The earliest night, not the latest, because it does not
change while its week or month ages: the night that went in as a week's copy stays that
week's copy until the week leaves the tier, and the one a month keeps is the one it first
had. A kept copy never moves, so a backup you have noted down is still there on the next
night.

Rotated every night with `7/4/3`, a kind settles on exactly 14 nights. On 30 May 2026
(a Saturday), after a nightly pass every night since 1 January:

| tier | nights kept |
| --- | --- |
| daily (7) | 24 May – 30 May |
| weekly (4) | 27 April, 4 May, 11 May, 18 May — each a Monday, the first night of its week |
| monthly (3) | 2 February, 2 March, 6 April — each the first night of its month left after the weekly tier |

Fourteen backups reaching back about three months past the weekly ones — to 2 February,
some four months in all — for the storage of 14 nights. A month keeps the night its first
week kept: 1 March was a Sunday, the last night of the week that began on 23 February, so it
was never that week's copy and was gone long before March reached the monthly tier.

[Passes without media](#passes-without-media) are measured against the **daily** window
only: a deploy pass older than the oldest daily night is deleted, even beside a weekly or
monthly night of the same date, or a folder would keep months of them.

Each store may set its own `KEEP_WEEKS` and `KEEP_MONTHS`, as it may its own `KEEP_NIGHTS`
([Several stores](Stores-several.md)): a short local window and a long off-site history
from the same passes. Each is a non-negative integer, or `E001`.

## Rotation runs last

Only after every part of the pass is uploaded. Making room before the new backup exists
is how a bad night becomes an empty bucket. A pass that failed before the end — an upload,
a lock read-back, a media volume — rotates nothing. A pass in which only a database dump
failed does rotate; the failed database's folder simply has no new night, so it keeps its
newest 14 as before.

## Passes without media

A deploy pass writes the databases and the configuration and leaves the media to the
night. Its objects are marked `.without-media.`:

```text
acme/prod/db/2026-03-10T14-12Z.without-media.sql.zst.age
```

Those objects **never count as nights**. They are kept while they are newer than the
oldest daily night kept, and deleted with it — a weekly or monthly night does not keep them. Counted naively, a fortnight of deploys over
failing nights would push out the last whole backup's database while its media stayed —
unrestorable.

With no whole night at all — a fresh setup that has seen only deploys — partial nights are
counted by themselves, so the folder cannot grow without bound.

The marker is written only in a project that has `MEDIA` configured. Where there is no
media, a pass without media *is* a whole backup, and counts as one.

## Versions and delete markers

On a versioned bucket, deleting a key without a version id only writes a **delete marker**:
the bytes stay stored and billed. Rotation therefore lists every version of every key in
the folder — markers included — and deletes each **by version id**. An expired key leaves
nothing behind.

## Locks: why one day less

A COMPLIANCE lock cannot be shortened or removed by anyone until it runs out — that is its
point. Rotation deletes the 15th-newest night; with 13-day locks, everything it reaches is
unlocked already. `E007` refuses `KEEP_NIGHTS <= LOCK_DAYS`:

```text
?: (offsite_backup.E007) RETENTION['KEEP_NIGHTS'] is 13, not more than OFFSITE_BACKUP['STORE']['LOCK_DAYS'] = 13.
	HINT: Keep at least one night more than the lock (e.g. 14 nights, 13 days), so rotation only ever deletes objects whose lock has run out.
```

A version that is still locked when rotation reaches it — a night of several passes, a
clock that disagrees — is **left for a later night** with a warning in the log
(`Backup still locked; left for a later night`), not a failure: a refused delete must not
fail the night over an object that is safe.

Only the daily tier must outlast the lock: weekly and monthly nights are older than the
oldest daily one, so whatever rotation deletes is past its lock already. The other side of
that, in this version: a weekly or monthly copy is locked for `LOCK_DAYS` like every object,
and **unlocked after that** — kept by rotation, but no longer protected from a deleting
credential. The copies you can count on after a compromise are still the last `LOCK_DAYS`
days ([Security model](Security-model.md)).

Changing either number:

* more nights — raise `KEEP_NIGHTS` and leave `LOCK_DAYS`;
* longer locks — raise `LOCK_DAYS`, change the **bucket's default rule** to the same number
  (`E006`), and keep `KEEP_NIGHTS` above it (`E007`). Objects already written keep the lock
  they were given.

## Depth

After rotation the pass reads how far back the backups reach — the number of distinct
nights under the first database's folder (the configuration's, without databases) whose
current version is an object, and the oldest of them:

```text
🗄 Kept: 14 days, since 2026-02-25
```

The count includes nights that only a deploy pass wrote, and the weekly and monthly ones: with
`7/4/3` it reads 14, since a date some four months back. It is best effort: a depth that
cannot be read is a line left out of the message, never a failed backup.

## Storage, roughly

A kind holds about `KEEP_NIGHTS + KEEP_WEEKS + KEEP_MONTHS` nights of whole backups plus
every deploy pass since the oldest daily one. Media is uploaded whole every night, so it dominates: 5 GB of media at 14
nights is about 70 GB stored. Incremental media is on the roadmap, for when that stops
being acceptable.
