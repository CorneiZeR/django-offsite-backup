"""What the store holds under the prefix, per kind — what ``backup_list`` and ``offsite-backup list`` print."""

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from offsite_backup import naming
from offsite_backup.pass_ import Components

__all__ = ('lines', 'rows')


def rows(parts: Components, kinds: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Per kind: objects, bytes, nights and the oldest, the newest stamp and its lock, objects without media."""
    prefix = parts.settings.prefix
    stored = parts.store.versions(f'{prefix}/')
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
        if parts.store.LOCKS and entry['newest_key']:
            held = stored[entry['newest_key']]
            current = next(version for version in held if version.latest and not version.marker)
            until = parts.store.locked_until(entry['newest_key'], current)
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


def lines(prefix: str, table: list[dict[str, Any]]) -> list[str]:
    """The table as text, a kind per line."""
    return [
        f'{prefix}/',
        *(
            f'  {row["kind"]}/: {row["nights"]} nights since {row["oldest_night"] or "-"}, '
            f'{row["objects"]} objects, {row["bytes"] / 1024**2:.1f} MB, newest {row["newest"]}'
            + (f', locked until {row["newest_locked_until"]}' if row['newest_locked_until'] else '')
            + (f', {row["partial_objects"]} without media' if row['partial_objects'] else '')
            for row in table
        ),
    ]
