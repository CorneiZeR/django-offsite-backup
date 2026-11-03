from datetime import date, datetime, timedelta, timezone

from offsite_backup.messages.default import format_outcome
from offsite_backup.outcome import Outcome, Summary
from offsite_backup.rotation import Depth
from offsite_backup.stores.base import Wrote

NOW = datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc)


def summary(**fields):
    base = {
        'stamp': '2026-03-10T03-00Z',
        'prefix': 'acme/prod',
        'reason': 'nightly',
        'media': True,
        'uploaded': [Wrote('k1', 1024**2), Wrote('k2', 1024**2)],
        'seconds': 12.4,
    }
    return Summary(**{**base, **fields})


def written(**fields):
    return format_outcome(
        Outcome(kind='written', prefix='acme/prod', env='prod', reason='nightly', summary=summary(**fields))
    )


def test_written_says_what_where_why_and_figures():
    """First line what and where, second why, figures, hashtags last."""
    text = written(depth=Depth(14, date(2026, 2, 25)), removed=['old'])
    assert text.splitlines() == [
        '💾 <b>Backup written</b>: <code>acme/prod</code>',
        '🌙 Nightly',
        '',
        '📦 Objects: 2',
        '⚖️ Size: 2.0 MB',
        '⏱ Took: 12s',
        '🗄 Kept: 14 days, since 2026-02-25',
        '🧹 Past the window: 1 removed',
        '',
        '#backup #prod #nightly #ok',
    ]


def test_unknown_depth_and_nothing_removed_leave_their_lines_out():
    """A depth that could not be read is a line left out."""
    text = written()
    assert 'Kept' not in text
    assert 'Past the window' not in text


def test_a_pass_without_media_says_so():
    """The deploy's pass tells the reader media is the nightly pass's."""
    assert '🖼 Media: left to the nightly pass' in written(media=False)


def test_a_failed_deploy_pass_says_the_deploy_stops():
    """Before a deploy, the reader learns the live release keeps serving."""
    text = format_outcome(Outcome(kind='failed', prefix='p', env='prod', reason='deploy', error='boom'))
    assert '🚀 Before a deploy' in text
    assert 'The deploy stops here; the live release keeps serving.' in text
    assert text.endswith('#backup #prod #deploy #failed')


def test_every_interpolated_value_is_escaped():
    """A chat rejects HTML that does not parse; an error with < in it must still arrive."""
    text = format_outcome(Outcome(kind='failed', prefix='<p>', env='prod', reason='<x>', error='a < b & c'))
    assert '<code>&lt;p&gt;</code>' in text
    assert '<pre>a &lt; b &amp; c</pre>' in text
    assert '🔁 &lt;x&gt;' in text
    assert '#_x_' in text


def test_the_error_is_cut_to_500_characters():
    """Enough to recognise the failure, not a traceback."""
    text = format_outcome(Outcome(kind='failed', prefix='p', reason='nightly', error='x' * 2000))
    assert 'x' * 500 + '</pre>' in text
    assert 'x' * 501 not in text


def test_missing_says_when_the_newest_was():
    """The morning check names the newest backup and its age."""
    text = format_outcome(
        Outcome(kind='missing', prefix='p', env='prod', newest=NOW - timedelta(hours=30), stale_hours=26, now=NOW)
    )
    assert 'Newest backup: 2026-03-09 03:00 UTC, 30 h ago' in text
    assert 'Expected within 26 h' in text
    assert text.endswith('#backup #prod #missing')


def test_missing_with_none_at_all():
    """No backup ever is said as such."""
    assert 'none at all' in format_outcome(Outcome(kind='missing', prefix='p', stale_hours=26, now=NOW))


def test_unchecked_is_as_loud_as_missing():
    """A check that could not look is tagged missing too."""
    text = format_outcome(Outcome(kind='unchecked', prefix='p', env='prod', error='denied <403>'))
    assert text.startswith('🚨 <b>Backups UNCHECKED</b>')
    assert '&lt;403&gt;' in text
    assert text.endswith('#missing')
