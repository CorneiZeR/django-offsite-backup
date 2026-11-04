import shutil
import subprocess
import sys

import pytest

from offsite_backup.encryption.pyrage import Pyrage, main
from offsite_backup.exceptions import PipelineError
from offsite_backup.pipeline import run_into

pyrage = pytest.importorskip('pyrage', reason='the [pyrage] extra is not installed')

AGE = shutil.which('age')
needs_age = pytest.mark.skipif(AGE is None, reason='the age binary is not installed: interoperability is not checked')


@pytest.fixture
def key(tmp_path):
    identity = pyrage.x25519.Identity.generate()
    path = tmp_path / 'identity.txt'
    path.write_text(f'# created: by the test\n# public key: {identity.to_public()}\n{identity}\n')
    return str(identity.to_public()), path


def seal(sealer, plain, sealed, extra=()):
    return run_into([sealer.pipeline(extra)], into=sealed, source=plain)


def ids(messages):
    return sorted(message.id for message in messages)


def test_a_pass_seals_and_opens_with_no_binary(key, tmp_path):
    """The stage is this interpreter: nothing to install but the extra."""
    recipient, identity = key
    sealer = Pyrage({'RECIPIENTS': [recipient]}, label='ENCRYPTION')
    assert sealer.binaries() == ()
    assert sealer.pipeline()[:3] == [sys.executable, '-m', 'offsite_backup.encryption.pyrage']
    plain = tmp_path / 'plain'
    plain.write_bytes(b'x' * 300_000)
    seal(sealer, plain, tmp_path / 'sealed')
    assert b'x' * 100 not in (tmp_path / 'sealed').read_bytes()
    run_into([sealer.unseal(str(identity))], into=tmp_path / 'opened', source=tmp_path / 'sealed')
    assert (tmp_path / 'opened').read_bytes() == plain.read_bytes()


def test_any_one_recipient_opens_it(key, tmp_path):
    """Several recipients and an extra one per call, as with the age binary."""
    recipient, _ = key
    other = pyrage.x25519.Identity.generate()
    other_file = tmp_path / 'other.txt'
    other_file.write_text(f'{other}\n')
    plain = tmp_path / 'plain'
    plain.write_bytes(b'wake me')
    seal(Pyrage({'RECIPIENTS': [recipient]}), plain, tmp_path / 'sealed', [str(other.to_public())])
    run_into([Pyrage({}).unseal(str(other_file))], into=tmp_path / 'opened', source=tmp_path / 'sealed')
    assert (tmp_path / 'opened').read_bytes() == b'wake me'


@needs_age
def test_what_pyrage_seals_the_age_binary_opens(key, tmp_path):
    """Interoperable one way: sealed here, `age -d` reads it."""
    recipient, identity = key
    plain = tmp_path / 'plain'
    plain.write_bytes(b'one way' * 1000)
    seal(Pyrage({'RECIPIENTS': [recipient]}), plain, tmp_path / 'sealed')
    opened = subprocess.run([AGE, '-d', '-i', str(identity), str(tmp_path / 'sealed')], capture_output=True, check=True)
    assert opened.stdout == plain.read_bytes()


@needs_age
def test_what_the_age_binary_seals_pyrage_opens(key, tmp_path):
    """And the other way: sealed by `age -r`, opened here."""
    recipient, identity = key
    plain = tmp_path / 'plain'
    plain.write_bytes(b'other way' * 1000)
    subprocess.run([AGE, '-r', recipient, '-o', str(tmp_path / 'sealed'), str(plain)], check=True)
    run_into([Pyrage({}).unseal(str(identity))], into=tmp_path / 'opened', source=tmp_path / 'sealed')
    assert (tmp_path / 'opened').read_bytes() == plain.read_bytes()


def test_a_wrong_identity_fails_the_stage_with_pyrage_words(key, tmp_path):
    """A refusal is a failed stage naming the error class; no key text in it."""
    recipient, _ = key
    plain = tmp_path / 'plain'
    plain.write_bytes(b'secret')
    seal(Pyrage({'RECIPIENTS': [recipient]}), plain, tmp_path / 'sealed')
    stranger = tmp_path / 'stranger.txt'
    stranger.write_text(f'{pyrage.x25519.Identity.generate()}\n')
    with pytest.raises(PipelineError, match='pyrage: DecryptError') as raised:
        run_into([Pyrage({}).unseal(str(stranger))], into=tmp_path / 'opened', source=tmp_path / 'sealed')
    assert 'AGE-SECRET-KEY' not in str(raised.value)


def test_an_identity_file_without_a_key_is_refused(tmp_path, capsys):
    """An empty file is said, not tried."""
    empty = tmp_path / 'empty.txt'
    empty.write_text('# nothing here\n')
    assert main(['open', str(empty)]) == 1
    assert 'no age or SSH identity' in capsys.readouterr().err


def test_a_bad_command_line_is_a_usage_error(capsys):
    """Neither seal with a recipient nor open with a file."""
    assert main(['seal']) == 2
    assert 'usage' in capsys.readouterr().err


def test_a_recipient_pyrage_cannot_read_is_e008():
    """It looks like a public key and is not one."""
    assert ids(Pyrage({'RECIPIENTS': ['age1notakey']}, label='ENCRYPTION').check()) == ['offsite_backup.E008']


def test_a_private_key_is_e008_as_for_the_binary():
    """The machine holds public keys only, whichever sealer seals."""
    found = Pyrage({'RECIPIENTS': ['AGE-SECRET-KEY-1XYZ']}, label='ENCRYPTION').check()
    assert ids(found) == ['offsite_backup.E008']
    assert 'PRIVATE' in found[0].msg


def test_a_good_recipient_has_no_finding(key, monkeypatch):
    """No E003 for a binary it does not run, on a machine without age."""
    monkeypatch.setenv('PATH', '/nonexistent')
    assert Pyrage({'RECIPIENTS': [key[0]]}, label='ENCRYPTION').check() == []


def test_a_whole_pass_with_pyrage(configured, fake_s3, settings, key, now, tmp_path):
    """Sealed by pyrage, downloaded and verified by the package itself."""
    from offsite_backup.pass_ import run
    from offsite_backup.restore import fetch

    settings.OFFSITE_BACKUP['ENCRYPTION'] = {
        'BACKEND': 'offsite_backup.encryption.pyrage.Pyrage',
        'RECIPIENTS': [key[0]],
    }
    run(now=now)
    restored = fetch('latest', into=tmp_path / 'out', identity=key[1], kinds=['db'])
    assert restored[0].verified


def test_the_extra_is_named(configured, settings, monkeypatch):
    """Without pyrage installed, E002 says which extra brings it."""
    import importlib.util

    from offsite_backup.checks import check_settings

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *a: None if name == 'pyrage' else real(name, *a))
    settings.OFFSITE_BACKUP['ENCRYPTION'] = {
        'BACKEND': 'offsite_backup.encryption.pyrage.Pyrage',
        'RECIPIENTS': ['age1x'],
    }
    [error] = [message for message in check_settings() if message.id == 'offsite_backup.E002']
    assert error.hint == 'pip install "django-offsite-backup[pyrage]"'
