# Upgrading

0.1.0 is the first release; there is nothing to upgrade from yet.

## Versioning

The package follows [semantic versioning](https://semver.org), with one rule spelled out
because it is the one that bites in a backup tool:

* **A settings rename, a removed setting, or a default that flips is a major version.**
  A default decides what a machine backs up and how long it keeps it; changing it under a
  minor bump would change production in silence.
* **Object names are a compatibility surface.** A release that changes how keys are named
  keeps reading the old names — for rotation, the morning check and restore — until
  objects under them have rotated out, and says so here.
* **Check codes are stable.** A code never changes meaning; a new check gets a new code.
* Anything else that changes behaviour is a minor version, and is in the
  [Changelog](Changelog.md).

While the major version is 0, a minor release may still carry a breaking change, and if it
does this page says exactly what to change.

## How to upgrade

1. Read the [Changelog](Changelog.md) between your version and the new one, and this page.
2. Bump the pin; run `python manage.py check --deploy` in the new image.
3. Run `backup_offsite --reason manual` once after deploying and read the message.
4. Run the [restore drill](Restore.md#the-monthly-drill) if the release touched naming,
   encryption or restore.
