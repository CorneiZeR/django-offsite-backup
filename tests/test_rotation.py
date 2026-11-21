from datetime import date, datetime, timedelta, timezone

import pytest

from offsite_backup import naming
from offsite_backup.rotation import Keep, depth, rotate
from offsite_backup.stores.filesystem import DirectoryStore
from offsite_backup.stores.s3 import S3Store
from offsite_backup.testing import FakeS3, use_fake_s3

NOW = datetime(2026, 3, 31, 3, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake():
    clock = {'now': NOW}
    fake = FakeS3(page_size=2, clock=lambda: clock['now'])
    fake.bucket('b', lock_enabled=False)
    fake.clock_state = clock
    with use_fake_s3(fake):
        yield fake


@pytest.fixture
def store(fake):
    return S3Store({'BUCKET': 'b', 'LOCK_DAYS': 0})


def night(day, hour=3, extra=''):
    return f'2026-03-{day:02d}T{hour:02d}-00Z{extra}'


def put(fake, *keys):
    for key in keys:
        fake.put_object(Bucket='b', Key=key, Body=b'x')


def keys(fake):
    return sorted(fake.buckets['b'].keys)


def test_keeps_the_newest_whole_nights_and_deletes_the_rest(fake, store):
    """KEEP nights survive; older ones go."""
    put(fake, *(f'p/db/{night(day)}.sql.zst.age' for day in range(1, 6)))
    result = rotate(store, 'p/db/', 3, now=NOW)
    assert result.removed == [f'p/db/{night(1)}.sql.zst.age', f'p/db/{night(2)}.sql.zst.age']
    assert len(keys(fake)) == 3


def test_several_runs_on_one_date_are_one_night(fake, store):
    """A retry or a run by hand on the same UTC date does not push a night out."""
    put(fake, *(f'p/db/{night(day)}.sql.zst.age' for day in (1, 2)))
    put(fake, f'p/db/{night(3, 3)}.sql.zst.age', f'p/db/{night(3, 12)}.sql.zst.age', f'p/db/{night(3, 22)}.sql.zst.age')
    rotate(store, 'p/db/', 2, now=NOW)
    assert keys(fake) == [
        f'p/db/{night(2)}.sql.zst.age',
        f'p/db/{night(3, 3)}.sql.zst.age',
        f'p/db/{night(3, 12)}.sql.zst.age',
        f'p/db/{night(3, 22)}.sql.zst.age',
    ]


def test_media_volumes_of_one_night_count_once(fake, store):
    """A night's media is several objects and one night."""
    put(fake, *(f'p/media/{night(day)}.{n:03d}.tar.age' for day in (1, 2) for n in (1, 2, 3)))
    rotate(store, 'p/media/', 2, now=NOW)
    assert len(keys(fake)) == 6


def test_partial_passes_do_not_count_as_nights(fake, store):
    """Deploys over failing nights cannot push out the last whole backup."""
    put(fake, f'p/db/{night(1)}.sql.zst.age')
    put(fake, *(f'p/db/{night(day, 12)}.without-media.sql.zst.age' for day in range(2, 20)))
    rotate(store, 'p/db/', 3, now=NOW)
    assert f'p/db/{night(1)}.sql.zst.age' in keys(fake)


def test_partial_passes_older_than_the_oldest_kept_whole_night_go(fake, store):
    """A partial pass is kept while newer than the oldest whole night kept, and goes with it."""
    put(fake, *(f'p/db/{night(day)}.sql.zst.age' for day in (5, 6, 7)))
    put(fake, f'p/db/{night(4, 12)}.without-media.sql.zst.age', f'p/db/{night(6, 12)}.without-media.sql.zst.age')
    rotate(store, 'p/db/', 2, now=NOW)
    assert keys(fake) == [
        f'p/db/{night(6)}.sql.zst.age',
        f'p/db/{night(6, 12)}.without-media.sql.zst.age',
        f'p/db/{night(7)}.sql.zst.age',
    ]


def test_with_no_whole_night_partial_nights_are_counted(fake, store):
    """A fresh setup that only ever saw deploys does not grow without bound."""
    put(fake, *(f'p/db/{night(day, 12)}.without-media.sql.zst.age' for day in range(1, 6)))
    rotate(store, 'p/db/', 2, now=NOW)
    assert len(keys(fake)) == 2


def test_every_version_and_delete_marker_of_an_expired_key_goes(fake, store):
    """A delete without a version only adds a marker; rotation removes the bytes."""
    old = f'p/db/{night(1)}.sql.zst.age'
    put(fake, old, old)
    fake.delete_object(Bucket='b', Key=old)
    put(fake, *(f'p/db/{night(day)}.sql.zst.age' for day in (2, 3)))
    rotate(store, 'p/db/', 2, now=NOW)
    assert old not in fake.buckets['b'].keys
    assert all(call[1]['VersionId'] for call in fake.calls if call[0] == 'delete_object' and call[1]['Key'] != old)


def test_a_locked_version_is_held_without_failing_the_night(fake, store):
    """A refused delete is left for a later night with a warning, not an error."""
    fake.bucket('b', lock_enabled=True, default_mode='COMPLIANCE', default_days=30)
    put(fake, f'p/db/{night(1)}.sql.zst.age')
    fake.bucket('b', lock_enabled=False)
    put(fake, *(f'p/db/{night(day)}.sql.zst.age' for day in (2, 3)))
    result = rotate(store, 'p/db/', 2, now=NOW)
    assert result.held == [f'p/db/{night(1)}.sql.zst.age']
    assert result.removed == []
    fake.clock_state['now'] = NOW + timedelta(days=40)
    assert rotate(store, 'p/db/', 2, now=NOW + timedelta(days=40)).removed == [f'p/db/{night(1)}.sql.zst.age']


def at(day, hour=3, extra=''):
    return f'p/db/{day:%Y-%m-%d}T{hour:02d}-00Z{extra}.sql.zst.age'


def kept_nights(fake, *, partial=False):
    return sorted({naming.night(key) for key in keys(fake) if naming.is_partial(key) == partial})


def every_night(first, last):
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def test_weekly_and_monthly_tiers_keep_the_earliest_night_of_each(fake, store):
    """Past the daily window: the earliest night of each of the newest weeks, then of the newest months."""
    put(fake, *(at(day) for day in every_night(date(2026, 1, 1), date(2026, 3, 31))))
    rotate(store, 'p/db/', Keep(7, weeks=4, months=3), now=NOW)
    daily = every_night(date(2026, 3, 25), date(2026, 3, 31))
    # the week of Monday 23 March holds the 23rd and 24th outside the daily window
    weekly = [date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16), date(2026, 3, 23)]
    # older than 2 March: 1 March is March's earliest, then February's and January's first
    monthly = [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    assert kept_nights(fake) == [*monthly, *weekly, *daily]


def test_a_month_tier_alone_counts_from_the_daily_window(fake, store):
    """With no weekly tier, months are counted among the nights older than the oldest daily one."""
    put(fake, *(at(day) for day in every_night(date(2026, 1, 1), date(2026, 3, 31))))
    rotate(store, 'p/db/', Keep(7, months=2), now=NOW)
    assert kept_nights(fake) == [date(2026, 2, 1), date(2026, 3, 1), *every_night(date(2026, 3, 25), date(2026, 3, 31))]


def test_nightly_passes_settle_on_exactly_the_three_tiers(fake, store):
    """150 nights, rotated each night: 7 daily, 4 weekly, 3 monthly — and a kept week's night never moves."""
    days = every_night(date(2026, 1, 1), date(2026, 5, 30))
    held: dict[tuple[int, int], set[date]] = {}
    for day in days:
        put(fake, at(day))
        rotate(store, 'p/db/', Keep(7, weeks=4, months=3), now=NOW)
        nights = kept_nights(fake)
        daily = sorted(nights, reverse=True)[:7]
        for night in nights:
            if night < min(daily):
                held.setdefault(night.isocalendar()[:2], set()).add(night)
    nights = kept_nights(fake)
    assert len(nights) == 7 + 4 + 3
    daily, weekly, monthly = nights[7:], nights[3:7], nights[:3]
    assert daily == every_night(date(2026, 5, 24), date(2026, 5, 30))
    assert weekly == [date(2026, 4, 27), date(2026, 5, 4), date(2026, 5, 11), date(2026, 5, 18)]
    assert monthly == [date(2026, 2, 2), date(2026, 3, 2), date(2026, 4, 6)]
    assert len({night.isocalendar()[:2] for night in weekly + monthly}) == 7
    assert len({(night.year, night.month) for night in monthly}) == 3
    # every week ever kept past the daily window was kept by one and the same night, start to end
    assert all(len(nights) == 1 for nights in held.values())


def test_partial_passes_are_measured_against_the_daily_window_only(fake, store):
    """A deploy pass older than the oldest daily night goes, even beside a weekly or monthly one."""
    put(fake, *(at(day) for day in every_night(date(2026, 1, 1), date(2026, 3, 31))))
    without = '.without-media'
    put(fake, at(date(2026, 3, 1), 12, without), at(date(2026, 3, 23), 12, without), at(date(2026, 3, 26), 12, without))
    rotate(store, 'p/db/', Keep(7, weeks=4, months=3), now=NOW)
    assert kept_nights(fake, partial=True) == [date(2026, 3, 26)]
    assert date(2026, 3, 23) in kept_nights(fake)


def test_with_no_whole_night_the_tiers_do_not_keep_partial_passes(fake, store):
    """A fresh setup of deploys only keeps its newest partial nights, as without tiers."""
    put(fake, *(at(day, 12, '.without-media') for day in every_night(date(2026, 1, 1), date(2026, 3, 31))))
    rotate(store, 'p/db/', Keep(2, weeks=4, months=3), now=NOW)
    assert kept_nights(fake, partial=True) == [date(2026, 3, 30), date(2026, 3, 31)]


def test_rotation_never_leaves_its_folder(fake, store):
    """``p/db/`` does not touch ``p/db-old/`` nor ``p-old/db/``."""
    put(fake, *(f'p/db/{night(day)}.sql.zst.age' for day in (1, 2, 3)))
    put(fake, *(f'p/db-old/{night(day)}.sql.zst.age' for day in (1, 2, 3)))
    put(fake, f'p-old/db/{night(1)}.sql.zst.age')
    rotate(store, 'p/db/', 1, now=NOW)
    assert len([key for key in keys(fake) if key.startswith('p/db-old/')]) == 3
    assert f'p-old/db/{night(1)}.sql.zst.age' in keys(fake)


def test_a_folder_without_a_trailing_slash_is_refused(store):
    """The trailing slash is the scope; without it ``db`` matches ``db-old``."""
    with pytest.raises(ValueError, match='ending in'):
        rotate(store, 'p/db', 1)


def test_a_key_without_a_stamp_is_never_touched(fake, store):
    """Only backups are rotated."""
    put(fake, 'p/db/README', *(f'p/db/{night(day)}.sql.zst.age' for day in (1, 2)))
    rotate(store, 'p/db/', 1, now=NOW)
    assert 'p/db/README' in keys(fake)


def test_depth_counts_downloadable_nights(fake, store):
    """A key whose current version is a delete marker is not a night anybody can restore."""
    put(fake, *(f'p/db/{night(day)}.sql.zst.age' for day in (1, 2, 3)))
    fake.delete_object(Bucket='b', Key=f'p/db/{night(1)}.sql.zst.age')
    assert depth(store, 'p/db/').nights == 2
    assert depth(store, 'p/db/').since == date(2026, 3, 2)


def test_depth_counts_a_key_restored_over_an_old_marker(fake, store):
    """IsLatest semantics: a marker in the history does not hide a newer version."""
    key = f'p/db/{night(1)}.sql.zst.age'
    put(fake, key)
    fake.delete_object(Bucket='b', Key=key)
    put(fake, key)
    assert depth(store, 'p/db/').nights == 1


def test_depth_that_cannot_be_read_is_none(store):
    """Best effort: the store unreadable is a line left out, not a failure."""
    broken = S3Store({'BUCKET': 'missing'})
    assert depth(broken, 'p/db/') is None


def test_the_same_algorithm_runs_on_a_directory_store(tmp_path):
    """Whole nights and partial passes, on a directory."""
    store = DirectoryStore({'ROOT': str(tmp_path)})
    for name in (f'{night(1)}.sql', f'{night(2)}.sql', f'{night(3)}.sql', f'{night(1, 12)}.without-media.sql'):
        path = tmp_path / 'p' / 'db' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x')
    rotate(store, 'p/db/', 2, now=NOW)
    assert sorted(path.name for path in (tmp_path / 'p' / 'db').iterdir()) == [f'{night(2)}.sql', f'{night(3)}.sql']
