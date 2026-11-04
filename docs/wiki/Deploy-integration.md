# Deploy integration

A migration changes the database; the backup that matters most is the one taken just
before it. Put one command in front of your migrations:

!!! tip "The deploy line"

    ```shell
    python manage.py backup_offsite --without-media --reason deploy
    ```

    Both flags, every time. `--reason deploy` is what makes a failure say "The deploy stops
    here"; without it the pass reads `DEFAULT_REASON` — `manual` unless you set it, "✋ By
    hand" in the chat.

* It runs **on the new image**, before `migrate`.
* **Non-zero stops the deploy.** The old release keeps serving, and the chat says
  "The deploy stops here; the live release keeps serving."
* It writes the databases and the configuration, not the media: a migration changes the
  database, never the media, and the media is what makes a pass long — measured, a whole
  pass with 5.2 GB of media took 1044 s, and a deploy waited for it. The media goes up every
  night anyway.
* Its objects carry the `.without-media.` marker (where `MEDIA` is configured), so a deploy
  at noon never counts as a night: it cannot push a whole night out of
  [retention](Retention.md), and it cannot hide a night that never ran from the
  [morning check](Monitoring.md).
* On a machine where backups are off it prints "nothing to do" and exits 0, so the same
  pipeline deploys a staging machine that has no keys.

**Forgetting `--reason`.** `backup_offsite` defaults to `--reason manual`: a person running
it by hand is the common case. A deploy step that leaves the flag out is read as a pass by
hand, and its failure message lacks the deploy line. As a safeguard, `--without-media`
without an explicit `--reason` prints a one-line notice to stderr:

```text
backup_offsite: the reason is 'manual'; before a deploy, pass --reason deploy (the message then says the deploy stops on failure)
```

A project that only ever runs the command from its deploy may set the default instead:

```python
OFFSITE_BACKUP = {..., 'DEFAULT_REASON': 'deploy'}
```

An explicit `--reason` always wins, and prints no notice.

## Where it runs, and what it needs there

Run it where the **new** release's settings and image are, not the old one's: a deploy
that adds a notifier token, a database or a binary should have its own pre-deploy pass use
them. The service it runs in needs:

* the backup's environment variables (store, recipient, notifier);
* a connection to the database;
* the deployment directory, **mounted read-only**, for the configuration archive — in this
  service **and** in the worker that runs the nightly pass. Both run a pass, and each
  archives what *its* container sees: a mount on one of them only gives the other an empty
  (or failing, with `REQUIRED`) configuration archive.

```yaml
services:
  web:
    image: registry.example.com/acme/web:${TAG}
    env_file: .env
    volumes:
      - ./:/app/deploy:ro
  worker:
    image: registry.example.com/acme/web:${TAG}
    command: celery -A acme worker
    env_file: .env
    volumes:
      - ./:/app/deploy:ro
```

Here `web` is the service the deploy runs the pass in (`docker compose run --rm web ...`,
below) and `worker` runs the nightly pass. `manage.py check --deploy` names a path missing
in the container it runs in: `W005`, or `E009` for a `REQUIRED` one. A plain `manage.py check`
and `migrate` say nothing about paths, so a container without the mount deploys quietly; in
the worker, which runs no system check, the pass logs what it left out.

## Docker Compose

```shell
set -euo pipefail
export TAG=1.42.0
docker compose pull
docker compose run --rm --no-deps web python manage.py backup_offsite --without-media --reason deploy
docker compose run --rm --no-deps web python manage.py migrate --no-input
docker compose up -d
```

`run --rm --no-deps` starts a one-off container of the new image without restarting
anything; `set -e` stops the script — and the deploy — on a failed backup.

## GitHub Actions

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - name: Deploy
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.DEPLOY_HOST }}
          username: deploy
          key: ${{ secrets.DEPLOY_SSH_KEY }}
          script: |
            set -euo pipefail
            cd /srv/acme
            export TAG=${{ github.ref_name }}
            docker compose pull
            docker compose run --rm --no-deps web python manage.py backup_offsite --without-media --reason deploy
            docker compose run --rm --no-deps web python manage.py migrate --no-input
            docker compose up -d
```

## GitLab CI

```yaml
deploy:
  stage: deploy
  environment: production
  rules:
    - if: $CI_COMMIT_TAG
  script:
    - |
      ssh deploy@acme.example.com "set -euo pipefail; cd /srv/acme; export TAG=$CI_COMMIT_TAG;
        docker compose pull;
        docker compose run --rm --no-deps web python manage.py backup_offsite --without-media --reason deploy;
        docker compose run --rm --no-deps web python manage.py migrate --no-input;
        docker compose up -d"
```

## Kubernetes, Helm, and the rest

The same command as a pre-upgrade hook or an init step that runs before the migration Job,
with `restartPolicy: Never` and `backoffLimit: 0`: a failed backup must fail the release,
not retry until it happens to pass.

## Before the first deploy

* Run `python manage.py check --deploy` in the new image as part of your pipeline's test
  or build stage, against production settings if you can: `E004` (a `pg_dump` older than
  the server) and `E005`/`E006` (a bucket without Object Lock or its default rule) are
  better found there than at the deploy. Run it once in the service the deploy runs the
  pass in, with the mount, for `E009` and `W005` (a required or an optional path missing
  there).
* Make sure the **CI job that runs your tests does not inherit the production notifier
  token** — see [Testing](Testing.md).
* Run the deploy pass once by hand and restore it ([Restore](Restore.md)).
