# Notifications

```python
'NOTIFY': [
    {
        'BACKEND': 'offsite_backup.notifiers.telegram.TelegramNotifier',
        'TOKEN': os.environ.get('BACKUP_TELEGRAM_TOKEN', ''),
        'CHAT_ID': os.environ.get('BACKUP_TELEGRAM_CHAT_ID', ''),
        'TOPIC_ID': os.environ.get('BACKUP_TELEGRAM_TOPIC_ID', ''),
    },
],
```

Every outcome — written, failed, missing, unchecked — is said where a person reads it.
`NOTIFY` is a list; every notifier in it gets every message.

## What the messages look like

HTML, written to be read on a phone among everything else in the chat: the first line is
what happened and where, the second why it ran, figures one per line, hashtags last — so a
thread of them is searchable by kind, and a failure never looks like the hundred successes
around it.

=== "Written"

    ```text
    💾 Backup written: acme/prod
    🌙 Nightly

    📦 Objects: 7
    ⚖️ Size: 5312.4 MB
    ⏱ Took: 1044s
    🗄 Kept: 14 days, since 2026-02-25
    🧹 Past the window: 7 removed

    #backup #prod #nightly #ok
    ```

    `Kept` is left out when the depth could not be read; `Past the window` when nothing was
    removed. A pass without media adds `🖼 Media: left to the nightly pass`.

=== "Failed"

    ```text
    🚨 Backup FAILED: acme/prod
    🚀 Before a deploy
    The deploy stops here; the live release keeps serving.

    1 database(s) not backed up, the rest went up — default: PipelineError: `pg_dump` exited with status 1: pg_dump: error: aborting because of server version mismatch

    #backup #prod #deploy #failed
    ```

    The error is cut at 500 characters. The deploy line appears only for `--reason deploy`.

=== "Missing"

    ```text
    🚨 Backup MISSING: acme/prod
    🕘 Morning check

    Newest backup: 2026-03-09 03:00 UTC, 30 h ago
    Expected within 26 h

    #backup #prod #missing
    ```

=== "Unchecked"

    ```text
    🚨 Backups UNCHECKED: acme/prod
    🕘 Morning check: the store could not be read

    ClientError: An error occurred (InvalidAccessKeyId) ...

    #backup #prod #missing
    ```

    As loud as missing, on purpose: a check that could not look has not seen a backup.

The second line comes from `--reason`:

| reason | line | hashtag |
| --- | --- | --- |
| `nightly` | 🌙 Nightly | `#nightly` |
| `deploy` | 🚀 Before a deploy | `#deploy` |
| `manual` (the command's default), `by hand` | ✋ By hand | `#manual` |
| `upgrade` | ⬆️ Before an upgrade | `#upgrade` |
| anything else | 🔁 *Reason* | `#reason`, non-alphanumerics replaced by `_` |

**Every interpolated value is escaped.** A chat rejects a message whose HTML does not
parse, and an error text with a `<` in it would otherwise be lost silently.

## Telegram

`pip install 'django-offsite-backup[telegram]'` — the Bot API over HTTPS with
[httpx2](https://pydantic.dev/docs/httpx2/).

| option | |
| --- | --- |
| `TOKEN`, `CHAT_ID` | required |
| `TOPIC_ID` | a forum topic (`message_thread_id`); empty for the main thread |
| `TIMEOUT` | seconds, default 10 |
| `API_URL` | default `https://api.telegram.org`; for a local Bot API server |

* A **topic id typed wrong** makes Telegram refuse the message. It is logged
  (`Telegram refused the backup message`, with Telegram's description) and nothing is
  posted to the main thread instead.
* A **429** is retried once after Telegram's `retry_after`, if that is 30 seconds or less.
* Neither the token nor the text is ever logged. A connection error is logged by its
  class only — the URL, and so the token, is in the message of several of them.

## Through django-aiogram

`pip install 'django-offsite-backup[aiogram]'` — the project's own bot, configured where
[django-aiogram](https://github.com/CorneiZeR/django-aiogram) is; no token here.

```python
'NOTIFY': [
    {
        'BACKEND': 'offsite_backup.notifiers.django_aiogram.TopicNotifier',
        'CHAT_ID': os.environ.get('BACKUP_TELEGRAM_CHAT_ID', ''),
        'TOPIC_ID': os.environ.get('BACKUP_TELEGRAM_TOPIC_ID', ''),
        # 'BOT': 'ops',   # one of TELEGRAM_BOTS; the default bot when absent
    },
],
```

It calls `bot.send_raw('send_message', ...)`, which talks to Telegram from the calling
process — synchronously where no bot loop runs (a Celery worker, a one-off deploy
container). In a process whose bot loop is running, `send_raw` only schedules the call, and
"delivered" means "handed over". Whether a refused send raises follows django-aiogram's own
`RAISE_EXCEPTION`; either way this notifier logs it and the backup goes on.

## The log

```python
'NOTIFY': [{'BACKEND': 'offsite_backup.notifiers.log.LogNotifier'}],
```

Writes to the `offsite_backup` logger: INFO for a written backup, ERROR for anything else,
the text in the record's `ob_text` extra field. It reaches no network, so it keeps working
under tests.

## A notifier never costs the backup

Every send is wrapped: a notifier that raises, times out or is misconfigured is logged
(`Notifier raised`, `Notifier not built`) and the pass goes on. A notifier outage never
turns a written backup into a failed one, and never hides a real failure behind its own.
The exit status of the command is the pass's, not the chat's.

## Tests never notify

The package's pytest plugin and `OffsiteBackupTestRunner` silence every notifier that can
reach the network for the whole test run, whatever the environment holds. See
[Testing](Testing.md).

## Your own notifier

```python
from offsite_backup.notifiers.base import Notifier


class SlackNotifier(Notifier):
    REQUIRED = ('WEBHOOK_URL',)

    def send(self, outcome, text):
        try:
            ...  # post text; outcome.kind is 'written', 'failed', 'missing' or 'unchecked'
        except Exception:
            return False
        return True
```

`send()` must not raise and answers whether the message was delivered. Leave
`REACHES_NETWORK = True` (the default) for anything that leaves the process, so tests
silence it.

## Your own format

`MESSAGES` is a dotted path to a callable taking an `Outcome` and returning the text, or
to a module holding one called `format_outcome`:

```python
OFFSITE_BACKUP = {..., 'MESSAGES': 'acme.backups.format_outcome'}
```

`offsite_backup.outcome.Outcome` carries `kind`, `prefix`, `env`, `name`, `reason`,
`summary` (objects uploaded, bytes, seconds, removed, failed databases, depth), `error`,
and for the morning check `newest`, `stale_hours` and `now`. A formatter that raises is
logged (`Backup message not formatted`) and nothing is sent — the outcome itself still
stands. `offsite_backup.messages.default` is the reference; its `reason_line()` and
`reason_tag()` are reusable.
