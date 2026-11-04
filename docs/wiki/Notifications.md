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

Every outcome — written, failed, missing, unchecked, and a drill passed or failed — is said
where a person reads it.
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

    The error is cut at 500 characters. The deploy line appears only for `--reason deploy`;
    `--reason upgrade` says instead `The upgrade stops here; the running <NAME> keeps serving.`

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

=== "Drill passed"

    ```text
    🧪 Restore drill passed: acme/prod
    📅 Backup of 2026-03-10T03-00Z, restored into drill

    🗃 default: 46 tables, 69 535 rows (live: 46 tables, 69 600 rows)
    🖼 media: 2 volumes, 12 034 files, 5312.4 MB
    ⚙️ config: 42 files, 0.4 MB
    ⏱ Took: 812s

    #backup #prod #drill #ok
    ```

    A line per database — the live figures only with `COMPARE_LIVE` — and per kind of archive.
    A table the live database has and the restore lacks adds
    `⚠️ Not in the backup, live now: public.new_table`. [Restore](Restore.md#the-monthly-drill).

=== "Drill failed"

    ```text
    🚨 Restore drill FAILED: acme/prod
    📅 Backup of 2026-03-10T03-00Z

    1 part(s) of 2026-03-10T03-00Z did not come back — acme/prod/db-analytics/2026-03-10T03-00Z.sql.zst.age: restore into 'drill': PipelineError: `psql` exited with status 3: ...

    🗃 default: 46 tables, 69 535 rows (live: 46 tables, 69 600 rows)

    #backup #prod #drill #failed
    ```

    Every object that did not come back is named with the step it failed at; what did come
    back is listed under it.

The second line comes from `--reason`:

| reason | line | hashtag |
| --- | --- | --- |
| `nightly` | 🌙 Nightly | `#nightly` |
| `deploy` | 🚀 Before a deploy | `#deploy` |
| `manual` (the command's default, see `DEFAULT_REASON`), `by hand` | ✋ By hand | `#manual` |
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
| `TOPIC_ID` | a forum topic (`message_thread_id`); `None`, empty or `0` for the main thread; not an integer is `E010` |
| `TIMEOUT` | seconds, default 10 |
| `API_URL` | default `https://api.telegram.org`; for a local Bot API server |

* A **topic id typed wrong** makes Telegram refuse the message. It is logged
  (`Telegram refused the backup message`, with Telegram's description) and nothing is
  posted to the main thread instead.
* A **429** is retried once after Telegram's `retry_after`, if that is 30 seconds or less.
* Neither the token nor the text is ever logged. A connection error is logged by its
  class only — the URL, and so the token, is in the message of several of them.
* The HTTP client logs every request with its URL at INFO, and the Bot API's URL holds the
  token: a filter on the `httpx2` logger writes `<redacted>` in its place (and in place of a
  webhook's URL), however the project configured its logging.

## Through django-aiogram

`pip install 'django-offsite-backup[aiogram]'` — the project's own bot, configured where
[django-aiogram](https://github.com/CorneiZeR/django-aiogram) is; no token here.

```python
'NOTIFY': [
    {
        'BACKEND': 'offsite_backup.notifiers.django_aiogram.TopicNotifier',
        'CHAT_ID': os.environ.get('BACKUP_TELEGRAM_CHAT_ID', ''),
        'TOPIC_ID': os.environ.get('BACKUP_TELEGRAM_TOPIC_ID', ''),
        # 'DELIVERY': 'queue',   # through the bot container; 'direct' by default
        # 'BOT': 'ops',          # one of TELEGRAM_BOTS; the default bot when absent
    },
],
```

`CHAT_ID` is required (`0` counts as unset); `TOPIC_ID` is validated by the same check as for
Telegram above (`E010`).

### Direct or queued

`DELIVERY` decides which of the bot's methods carries the message:

| | `'direct'` (default) | `'queue'` |
| --- | --- | --- |
| calls | `bot.send_raw('send_message', ...)` | `bot.send('send_message', ...)` |
| who talks to Telegram | the process running the pass — the worker, the deploy's one-off container | the bot container, which consumes django-aiogram's queue |
| the bot token | must be in the worker's (and the deploy container's) environment | stays in the bot container; the worker needs only the queue |
| `True` from the notifier means | sent — synchronously where no bot loop runs; in a process whose bot loop runs, `send_raw` only schedules the call, and it means "handed over" | **queued**: delivery depends on the bot container running |
| a refusal (wrong topic, bot not in the chat) | logged here — whether it raises follows django-aiogram's `RAISE_EXCEPTION`; either way the backup goes on | logged by the bot container, not here |
| django-aiogram's queue: rate limiting, event log, correlation ids | bypassed, with the queue | applied |

Choose `direct` when the process running the pass may hold the token, and the message
should not depend on another container. Choose `queue` when the project runs a dedicated
bot container and keeps the token there: one place talks to Telegram, under one rate limit
— and while that container is down, backup messages are not delivered.

### Another bot wrapper: `BOT_FACTORY`

```python
'BOT_FACTORY': 'acme.bots.backup_bot',
```

A dotted path to a callable that takes no arguments and returns an object with
`send_raw(method, **kwargs)` and/or `send(method, **kwargs)` — whichever `DELIVERY` calls.
It replaces `BOT`: the escape hatch for an older django-aiogram, or any other wrapper that
can be adapted to those two methods. It is called at every send; a path that does not
import, or does not name a callable, is `E002`.

## A webhook

`pip install 'django-offsite-backup[webhook]'` — a JSON `POST` to any URL, with
[httpx2](https://pydantic.dev/docs/httpx2/): an incident tool, a chat that takes webhooks,
your own endpoint.

```python
'NOTIFY': [
    {
        'BACKEND': 'offsite_backup.notifiers.webhook.WebhookNotifier',
        'URL': os.environ.get('BACKUP_WEBHOOK_URL', ''),
        'SECRET': os.environ.get('BACKUP_WEBHOOK_SECRET', ''),
        # 'HEADERS': {'Authorization': 'Bearer ...'},
        # 'TIMEOUT': 10, 'RETRIES': 2,
    },
],
```

| option | |
| --- | --- |
| `URL` | required; `http://` or `https://`, else `E010` |
| `SECRET` | optional; signs every body (below) |
| `HEADERS` | a mapping of extra headers, e.g. an `Authorization`; not a mapping is `E010` |
| `TIMEOUT` | seconds per attempt, default 10; not a positive number is `E010` |
| `RETRIES` | attempts after the first on a network error, a 429 or a 5xx, default 2, waiting 1 s, 2 s, 4 s…; not a whole number ≥ 0 is `E010` |

The body is the outcome as data beside the formatted text, so a receiver can route on
`kind` and still show the message:

```json
{
  "kind": "written",
  "prefix": "acme/prod",
  "env": "prod",
  "name": "",
  "reason": "nightly",
  "error": "",
  "summary": {"stamp": "2026-03-10T03-00Z", "bytes": 5570142208, "keys": ["acme/prod/db/2026-03-10T03-00Z.sql.zst.age", "..."], "seconds": 1044.2, "failed": {}, "removed": [], "depth": {"nights": 14, "since": "2026-02-25"}, "...": "..."},
  "drill": null,
  "newest": null,
  "text": "💾 <b>Backup written</b>: ...",
  "sent_at": "2026-03-10T03:17:24.981Z"
}
```

`kind` is `written`, `failed`, `missing`, `unchecked`, `drilled` or `drill_failed`; times are
ISO 8601. With a `SECRET`, every request carries

```text
X-Offsite-Backup-Signature: sha256=<hex HMAC-SHA256 of the exact body bytes, keyed with SECRET>
```

— recompute it over the raw body and compare in constant time before trusting the body:

```python
import hashlib, hmac

expected = 'sha256=' + hmac.new(secret.encode(), request.body, hashlib.sha256).hexdigest()
if not hmac.compare_digest(expected, request.headers['X-Offsite-Backup-Signature']):
    return 403
```

`sent_at` is inside the signed body, so a receiver can also refuse a replay of an old one.
Any other answer than a 2xx, 429 or 5xx is final. The URL is **never logged** — a webhook
URL often carries its own token — nor the body or the secret: a failure is logged as
`Webhook refused or unreachable` with the status or the error's class.

## The log

```python
'NOTIFY': [{'BACKEND': 'offsite_backup.notifiers.log.LogNotifier'}],
```

Writes to the `offsite_backup` logger: INFO for a written backup and a passed drill, ERROR for anything else,
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
            ...  # post text; outcome.kind is 'written', 'failed', 'missing', 'unchecked', 'drilled' or 'drill_failed'
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
for the morning check `newest`, `stale_hours` and `now`, and for a drill `drill` (the
stamp, the target, a record per database and per archive, what failed, seconds).
`Outcome.as_dict()` is all of it as JSON-ready data — what the webhook sends. A formatter that raises is
logged (`Backup message not formatted`) and nothing is sent — the outcome itself still
stands. `offsite_backup.messages.default` is the reference; its `reason_line()` and
`reason_tag()` are reusable.
