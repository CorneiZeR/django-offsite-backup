# Testing

Two things a project's tests need from this package: that **they never post to a real
chat**, and a way to test their own backup wiring without S3, `age` or a network.

## Tests never notify

A CI job that inherited the production bot token from the project's variables once posted
the test suite's backup messages to the team's chat. So:

* **pytest**: installing the package registers a pytest plugin (`pytest11` entry point)
  that silences every notifier able to reach the network, from the first test to the
  last. Nothing to configure.
* **Django's test runner**: use the shipped runner, or mix it into yours:

    ```python
    TEST_RUNNER = 'offsite_backup.testing.OffsiteBackupTestRunner'
    ```

    ```python
    from offsite_backup.testing import OffsiteBackupTestRunner


    class ProjectRunner(OffsiteBackupTestRunner, YourRunner): ...
    ```

"Silenced" means: a notifier whose class says `REACHES_NETWORK = True` — Telegram,
django-aiogram, and any third-party notifier unless it says otherwise — is not called.
`LogNotifier` still logs, and `capture()` still sees every message. Under a test run that
is *not* silenced and has `NOTIFY` set, `manage.py check` warns `W004`.

**And still: keep production notifier tokens out of the CI test job.** The silence covers
this package; it does not cover your own code, other packages, or a script that runs
`manage.py` outside the test runner. In GitHub Actions, give the token only to the deploy
job's `environment`; in GitLab CI, make the variable *protected* and scope it to the
production environment, so the test job never sees it.

To silence a block by hand (a script, a REPL):

```python
from offsite_backup.testing import silence

with silence():
    ...
```

## Asserting on messages: `capture()`

```python
from offsite_backup.testing import capture


def test_deploy_pass_says_media_is_left(configured):
    with capture() as said:
        call_command('backup_offsite', '--without-media', '--reason', 'deploy')
    outcome, text = said[-1]
    assert outcome.kind == 'written'
    assert 'Media: left to the nightly pass' in text
```

`capture()` collects every `(outcome, text)` formatted inside the block, whatever
`NOTIFY` holds — silenced or not, with or without notifiers.

## A fake S3: `FakeS3` and `use_fake_s3`

```python
from offsite_backup.testing import FakeS3, use_fake_s3


@pytest.fixture
def fake_s3():
    fake = FakeS3(page_size=2, multipart_threshold=1024)
    fake.bucket('backups')  # Object Lock on, default COMPLIANCE 13 days
    with use_fake_s3(fake):
        yield fake
```

Inside `use_fake_s3()` every S3 client the package builds — the store's and `S3Source`'s —
is the fake. It is in memory and behaves like the providers backups are kept at,
**including where they bite**:

* versions and delete markers; a delete without a version id adds a marker;
* Object Lock: a locked version refuses deletion (`AccessDenied`);
* pagination, `page_size` items per page, so loops over pages are exercised;
* a single PUT without lock arguments lands GOVERNANCE, whatever the bucket's default;
* a multipart upload (from `multipart_threshold` bytes) keeps the mode but takes its date
  from the bucket's default rule — none without one;
* a single PUT above `put_limit` is refused with `EntityTooLarge`.

`fake.bucket(name, versioned=..., lock_enabled=..., default_mode=..., default_days=...)`
creates or reconfigures a bucket; `fake.buckets[name].keys` holds every version;
`fake.calls` records uploads and deletes; `FakeS3(clock=...)` moves time for lock expiry.
`put_object()` seeds a media bucket for `S3Source`.

## No `age` on the CI machine: `Passthrough`

```python
'ENCRYPTION': {'BACKEND': 'offsite_backup.testing.Passthrough', 'RECIPIENTS': ['age1test']},
'COMPRESS': [],
```

`cat` instead of `age`, both ways. It raises `W002` on every check — nothing it writes is
encrypted — so it cannot reach production unnoticed. An empty `COMPRESS` drops the `zstd`
stage as well.

## Media on disk: `media_tree`

```python
from offsite_backup.testing import media_tree

root = media_tree(tmp_path / 'media', {'a.jpg': b'a' * 300, 'sub/b.png': b'b' * 300})
```

Writes `{relative path: bytes}` under a root and returns it — a `DirectorySource` `ROOT`.

## A whole wiring test

```python
import pytest
from django.core.management import call_command

from offsite_backup.testing import FakeS3, capture, media_tree, use_fake_s3


@pytest.fixture
def configured(settings, tmp_path):
    fake = FakeS3()
    fake.bucket('backups')
    settings.OFFSITE_BACKUP = {
        'PREFIX': 'acme/test',
        'STORE': {
            'BACKEND': 'offsite_backup.stores.s3.S3Store',
            'BUCKET': 'backups',
            'ACCESS_KEY': 'k',
            'SECRET_KEY': 's',
        },
        'ENCRYPTION': {'BACKEND': 'offsite_backup.testing.Passthrough', 'RECIPIENTS': ['age1test']},
        'COMPRESS': [],
        'MEDIA': [
            {
                'BACKEND': 'offsite_backup.media.filesystem.DirectorySource',
                'ROOT': str(media_tree(tmp_path / 'media', {'a.jpg': b'a'})),
            },
        ],
    }
    with use_fake_s3(fake):
        yield fake


def test_nightly_pass_writes_a_volume(configured):
    with capture() as said:
        call_command('backup_offsite', '--reason', 'nightly')
    keys = list(configured.buckets['backups'].keys)
    assert any(key.startswith('acme/test/media/') and key.endswith('.001.tar.age') for key in keys)
    assert said[-1][0].kind == 'written'
```

A database source needs `pg_dump` and a real PostgreSQL; test that in an integration job,
or stand a `DatabaseSource` subclass of your own in for it.

## The guard in tests

`run_nightly()` claims the night in the `GUARD['CACHE']`. Tests that call it twice in one
"night" see the second skipped — clear the cache between tests, or pass
`'GUARD': {'ENABLED': False}`.
