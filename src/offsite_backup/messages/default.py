"""The shipped message format: HTML for a chat, read on a phone among everything else.

The first line says what happened and where; the second why it ran; figures one per line;
hashtags last, so a thread of them is searched by kind and a failure never looks like the
hundred successes around it. Every interpolated value is escaped: a chat rejects a message
whose HTML does not parse, and an error text with ``<`` in it would silently be lost.
"""

from html import escape

from offsite_backup.outcome import DRILL_FAILED, DRILLED, FAILED, MISSING, UNCHECKED, WRITTEN, Drill, Outcome

__all__ = ('ERROR_CHARS', 'REASONS', 'format_outcome', 'reason_line', 'reason_tag')

#: why a pass ran -> (icon, label, hashtag)
REASONS: dict[str, tuple[str, str, str]] = {
    'nightly': ('🌙', 'Nightly', 'nightly'),
    'deploy': ('🚀', 'Before a deploy', 'deploy'),
    'manual': ('✋', 'By hand', 'manual'),
    'by hand': ('✋', 'By hand', 'manual'),
    'upgrade': ('⬆️', 'Before an upgrade', 'upgrade'),
}
#: how much of an error a message carries: enough to recognise it, not a traceback
ERROR_CHARS = 500


def _word(text: str) -> str:
    """Anything a hashtag cannot hold, replaced."""
    return ''.join(char if char.isalnum() else '_' for char in text)


def _where(outcome: Outcome) -> str:
    return f'<code>{escape(outcome.prefix or outcome.name or "?")}</code>'


def reason_line(reason: str) -> str:
    """``🌙 Nightly``, or ``🔁 <Reason>`` for one the table does not know."""
    icon, label, _ = REASONS.get(reason, ('🔁', reason.capitalize() or 'Unknown', ''))
    return f'{icon} {escape(label)}'


def reason_tag(reason: str) -> str:
    """``#nightly``, or ``#<reason>`` for one the table does not know."""
    known = REASONS.get(reason)
    return f'#{escape(known[2] if known else _word(reason) or "unknown")}'


def _tags(outcome: Outcome, *tags: str) -> str:
    env = _word(outcome.env)
    return ' '.join(['#backup', *([f'#{escape(env)}'] if env else []), *tags])


def _written(outcome: Outcome) -> str:
    summary = outcome.summary
    if summary is None:
        msg = 'a written outcome carries its summary'
        raise ValueError(msg)
    lines = [
        f'💾 <b>Backup written</b>: {_where(outcome)}',
        reason_line(outcome.reason),
        '',
        f'📦 Objects: {summary.objects}',
        f'⚖️ Size: {summary.bytes / 1024**2:.1f} MB',
        f'⏱ Took: {summary.seconds:.0f}s',
    ]
    if len(summary.stores) > 1:
        lines.append(f'🗃 Stores: {escape(", ".join(summary.stores))}')
    if summary.depth is not None:
        lines.append(f'🗄 Kept: {summary.depth.nights} days, since {summary.depth.since:%Y-%m-%d}')
    if not summary.media:
        lines.append('🖼 Media: left to the nightly pass')
    if summary.removed:
        lines.append(f'🧹 Past the window: {len(summary.removed)} removed')
    lines += ['', _tags(outcome, reason_tag(outcome.reason), '#ok')]
    return '\n'.join(lines)


def _failed(outcome: Outcome) -> str:
    lines = [f'🚨 <b>Backup FAILED</b>: {_where(outcome)}', reason_line(outcome.reason)]
    if outcome.reason == 'deploy':
        lines.append('The deploy stops here; the live release keeps serving.')
    elif outcome.reason == 'upgrade':
        lines.append(f'The upgrade stops here; the running {escape(outcome.name or "service")} keeps serving.')
    lines += ['', f'<pre>{escape(outcome.error[:ERROR_CHARS])}</pre>', '']
    lines.append(_tags(outcome, reason_tag(outcome.reason), '#failed'))
    return '\n'.join(lines)


def _missing(outcome: Outcome) -> str:
    if outcome.newest is None or outcome.now is None:
        seen = 'none at all'
    else:
        hours = (outcome.now - outcome.newest).total_seconds() / 3600
        seen = f'{outcome.newest:%Y-%m-%d %H:%M} UTC, {hours:.0f} h ago'
    return '\n'.join(
        [
            f'🚨 <b>Backup MISSING</b>: {_where(outcome)}',
            '🕘 Morning check',
            '',
            f'Newest backup: {seen}',
            f'Expected within {outcome.stale_hours:g} h',
            '',
            _tags(outcome, '#missing'),
        ]
    )


def _unchecked(outcome: Outcome) -> str:
    return '\n'.join(
        [
            f'🚨 <b>Backups UNCHECKED</b>: {_where(outcome)}',
            '🕘 Morning check: the store could not be read',
            '',
            f'<pre>{escape(outcome.error[:ERROR_CHARS])}</pre>',
            '',
            _tags(outcome, '#missing'),
        ]
    )


def _count(number: int) -> str:
    """``69 535``: thousands apart, readable on a phone."""
    return f'{number:,}'.replace(',', ' ')


def _drill_lines(drill: Drill) -> list[str]:
    lines = []
    for database in drill.databases:
        line = f'🗃 {escape(database.alias)}: {_count(database.tables)} tables, {_count(database.rows)} rows'
        if database.live_tables is not None and database.live_rows is not None:
            line += f' (live: {_count(database.live_tables)} tables, {_count(database.live_rows)} rows)'
        lines.append(line)
        if database.missing:
            shown = ', '.join(escape(name) for name in database.missing[:5])
            more = f' and {len(database.missing) - 5} more' if len(database.missing) > 5 else ''  # noqa: PLR2004
            lines.append(f'⚠️ Not in the backup, live now: {shown}{more}')
    kinds: dict[str, list[int]] = {}
    for archive in drill.archives:
        found = kinds.setdefault(archive.kind, [0, 0, 0])
        found[0] += 1
        found[1] += archive.files
        found[2] += archive.bytes
    for kind, (volumes, files, size) in kinds.items():
        icon = '⚙️' if kind == 'config' else '🖼'
        parts = [f'{volumes} volumes'] if kind != 'config' else []
        parts += [f'{_count(files)} files', f'{size / 1024**2:.1f} MB']
        lines.append(f'{icon} {escape(kind)}: {", ".join(parts)}')
    return lines


def _drilled(outcome: Outcome) -> str:
    drill = outcome.drill
    if drill is None:
        msg = 'a drill outcome carries its drill'
        raise ValueError(msg)
    lines = [
        f'🧪 <b>Restore drill passed</b>: {_where(outcome)}',
        f'📅 Backup of {escape(drill.stamp)}, restored into <code>{escape(drill.target or "-")}</code>',
        '',
        *_drill_lines(drill),
        f'⏱ Took: {drill.seconds:.0f}s',
        '',
        _tags(outcome, '#drill', '#ok'),
    ]
    return '\n'.join(lines)


def _drill_failed(outcome: Outcome) -> str:
    drill = outcome.drill
    lines = [f'🚨 <b>Restore drill FAILED</b>: {_where(outcome)}']
    if drill is not None and drill.stamp:
        lines.append(f'📅 Backup of {escape(drill.stamp)}')
    lines += ['', f'<pre>{escape(outcome.error[:ERROR_CHARS])}</pre>']
    if drill is not None and (drill.databases or drill.archives):
        lines += ['', *_drill_lines(drill)]
    lines += ['', _tags(outcome, '#drill', '#failed')]
    return '\n'.join(lines)


_FORMATS = {
    WRITTEN: _written,
    FAILED: _failed,
    MISSING: _missing,
    UNCHECKED: _unchecked,
    DRILLED: _drilled,
    DRILL_FAILED: _drill_failed,
}


def format_outcome(outcome: Outcome) -> str:
    """The message for one outcome."""
    return _FORMATS[outcome.kind](outcome)
