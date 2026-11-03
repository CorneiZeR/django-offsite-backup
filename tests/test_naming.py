from datetime import date, datetime, timedelta, timezone

from offsite_backup import naming


def test_stamp_is_utc_and_sortable():
    """A stamp is the UTC minute, in a form that sorts as text."""
    kyiv = timezone(timedelta(hours=3))
    assert naming.stamp(datetime(2026, 1, 2, 5, 7, tzinfo=kyiv)) == '2026-01-02T02-07Z'
    assert naming.stamp(datetime(2026, 1, 2, 3, 0, tzinfo=timezone.utc)) < naming.stamp(
        datetime(2026, 1, 10, 3, 0, tzinfo=timezone.utc)
    )


def test_parse_takes_every_part_apart():
    """Marker, volume number and suffix are read back from a full key."""
    name = naming.parse('p/media/2026-01-02T03-00Z.007.tar.age')
    assert name is not None
    assert (name.stamp, name.marker, name.number, name.suffix) == ('2026-01-02T03-00Z', '', 7, 'tar.age')
    partial = naming.parse('p/db/2026-01-02T03-00Z.without-media.sql.zst.age')
    assert partial is not None
    assert (partial.marker, partial.number, partial.suffix) == ('without-media', None, 'sql.zst.age')


def test_a_key_without_a_stamp_is_not_a_backup():
    """Nothing without a stamp is ever a backup."""
    assert naming.parse('p/db/notes.txt') is None
    assert naming.night('p/db/2026-01-02.sql') is None


def test_night_is_the_utc_date():
    """Two runs on one UTC date are one night."""
    assert naming.night('p/db/2026-01-02T00-01Z.sql.zst.age') == date(2026, 1, 2)
    assert naming.night('p/db/2026-01-02T23-59Z.sql.zst.age') == date(2026, 1, 2)


def test_object_name_round_trips():
    """What object_name writes, parse reads."""
    written = naming.object_name('2026-01-02T03-00Z', 'tar.age', partial=True, number=12)
    assert written == '2026-01-02T03-00Z.without-media.012.tar.age'
    assert naming.is_partial(written)


def test_kinds_are_siblings_not_children():
    """``db-<alias>`` is a sibling of ``db``, so ``db/`` rotation never counts it."""
    assert naming.db_kind('default') == 'db'
    assert naming.db_kind('tenant1') == 'db-tenant1'
    assert naming.media_kind('') == 'media'
    assert naming.media_kind('avatars') == 'media-avatars'
    assert naming.folder('p', 'db') == 'p/db/'
    assert not naming.join('p', 'db-tenant1', 'x').startswith(naming.folder('p', 'db'))
