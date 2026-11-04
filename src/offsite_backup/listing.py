"""What the store holds under the prefix, per kind — what ``backup_list`` and ``offsite-backup list`` print."""

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from offsite_backup import naming
from offsite_backup.pass_ import Components
from offsite_backup.stores.base import Store

__all__ = ('lines', 'rows')


def rows(parts: Components, kinds: Sequence[str] = (), *, store: Store | None = None) -> list[dict[str, Any]]:
    """Per kind: objects, bytes, nights and the oldest, the newest stamp and its lock, objects without media.

    ``store`` is one of several stores; the first by default.
    """
    prefix = parts.settings.prefix
    store = store or parts.store
    stored = store.versions(f'{prefix}/')
    found: dict[str, dict[str, Any]] = defaultdict(
        lambda: {'objects': 0, 'bytes': 0, 'nights': set(), 'partial': 0, 'newest': '', 'newest_key': ''}
    )
    for key, held in stored.items():
        current = next((version for version in held if version.latest and not version.marker), None)
        name = naming.parse(key)
        if current is None or name is None:
            continue
        kind = key.removeprefix(f'{prefix}/').rsplit('/', 1)[0]
        if kinds and kind not in kinds:
            continue
        entry = found[kind]
        entry['objects'] += 1
        entry['bytes'] += current.size
        entry['partial'] += name.marker == naming.WITHOUT_MEDIA
        if name.marker != naming.WITHOUT_MEDIA:
            entry['nights'].add(name.night.isoformat())
        if name.stamp > entry['newest']:
            entry['newest'], entry['newest_key'] = name.stamp, key
    table = []
    for kind in sorted(found):
        entry = found[kind]
        locked = None
        if store.LOCKS and entry['newest_key']:
            held = stored[entry['newest_key']]
            current = next(version for version in held if version.latest and not version.marker)
            until = store.locked_until(entry['newest_key'], current)
            locked = until.isoformat() if until else None
        nights = sorted(entry['nights'])
        table.append(
            {
                'kind': kind,
                'objects': entry['objects'],
                'bytes': entry['bytes'],
                'nights': len(nights),
                'oldest_night': nights[0] if nights else None,
                'newest': entry['newest'],
                'partial_objects': entry['partial'],
                'newest_locked_until': locked,
            }
        )
    return table


def lines(prefix: str, table: list[dict[str, Any]], *, store: str = '') -> list[str]:
    """The table as text, a kind per line; ``store`` names which, among several."""
    return [
        f'{prefix}/' + (f'  (store {store})' if store else ''),
        *(
            f'  {row["kind"]}/: {row["nights"]} nights since {row["oldest_night"] or "-"}, '
            f'{row["objects"]} objects, {row["bytes"] / 1024**2:.1f} MB, newest {row["newest"]}'
            + (f', locked until {row["newest_locked_until"]}' if row['newest_locked_until'] else '')
            + (f', {row["partial_objects"]} without media' if row['partial_objects'] else '')
            for row in table
        ),
    ]
