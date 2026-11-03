# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[private vulnerability reporting](https://github.com/CorneiZeR/django-offsite-backup/security/advisories/new),
not in a public issue. Expect an acknowledgement within a few days.

## Supported versions

The latest minor release receives security fixes.

## What the package promises

- The server holds **public** keys only; a backup it wrote cannot be read with anything on it.
- Every object in an S3 store is written with Object Lock COMPLIANCE and the lock is read back;
  the key the server holds cannot delete a locked object.
- Credentials reach subprocesses through the environment, never the command line, and no
  notifier logs a token or the message body.

The [Security model](https://corneizer.github.io/django-offsite-backup/latest/Security-model/)
page describes the threat model and its limits.
