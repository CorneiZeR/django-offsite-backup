import pytest

from offsite_backup.exceptions import PipelineError
from offsite_backup.pipeline import feed, run_into


def test_output_reaches_the_file(tmp_path):
    """Every stage runs, in order, into the target."""
    out = run_into([['printf', 'hello'], ['tr', 'a-z', 'A-Z']], into=tmp_path / 'out')
    assert out.read_bytes() == b'HELLO'


def test_a_first_stage_dying_mid_stream_fails_the_pipeline(tmp_path):
    """pipefail: the last stage succeeding does not hide a dump that died halfway."""
    with pytest.raises(PipelineError, match='`sh` exited with status 3'):
        run_into([['sh', '-c', 'printf partial; echo broken >&2; exit 3'], ['cat']], into=tmp_path / 'out')


def test_the_error_carries_the_failing_stages_stderr(tmp_path):
    """What the tool said is in the error, so the message says why."""
    with pytest.raises(PipelineError, match='broken'):
        run_into([['sh', '-c', 'echo broken >&2; exit 3']], into=tmp_path / 'out')


def test_a_missing_binary_is_named(tmp_path):
    """A stage that cannot start names its program."""
    with pytest.raises(PipelineError, match='no-such-binary-here'):
        run_into([['printf', 'x'], ['no-such-binary-here']], into=tmp_path / 'out')


def test_secrets_travel_in_the_environment(tmp_path):
    """env reaches the stage; nothing needs to be in argv."""
    out = run_into([['sh', '-c', 'printf "$PGPASSWORD"']], into=tmp_path / 'out', env={'PGPASSWORD': 'pw'})
    assert out.read_text() == 'pw'


def test_feed_streams_into_the_pipeline(tmp_path):
    """What is written to the yielded stdin goes through every stage."""
    with feed([['tr', 'a-z', 'A-Z']], into=tmp_path / 'out') as stdin:
        stdin.write(b'abc')
    assert (tmp_path / 'out').read_bytes() == b'ABC'


def test_feed_reports_a_stage_that_died(tmp_path):
    """A sealer that exits non-zero fails the volume even though every write succeeded."""
    with pytest.raises(PipelineError), feed([['sh', '-c', 'cat >/dev/null; exit 2']], into=tmp_path / 'out') as stdin:
        stdin.write(b'abc')
