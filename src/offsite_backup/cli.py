"""``offsite-backup``: the same passes on a server with no Django project.

The setting is a TOML file whose top level is what ``OFFSITE_BACKUP`` holds in a Django
project — the same keys, the same blocks, the same defaults (``[STORE]``, ``[[NOTIFY]]``).
Strings may name environment variables, ``${NAME}`` or ``${NAME:-default}``, so secrets stay
in the environment or in ``--env-file`` files (``KEY=value`` lines, later files winning over
earlier ones and over the process environment) and never in the file itself or in argv.
``$$`` is a literal dollar. Write paths absolute: a relative one is the working directory's,
as under Django — except ``RENDER_ENV``, read against the file's directory where Django would
read ``BASE_DIR``.

Exit codes: 0 done (or nothing to do: backups off), 1 failed, 2 a usage error;
``status`` answers 1 for stale and 2 for an unreadable store; ``checks`` answers 3 for off.
"""

import argparse
import json
import logging
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from offsite_backup import __version__, conf
from offsite_backup.exceptions import BackupError

__all__ = ('CONFIG_ENV', 'expand', 'load_config', 'main', 'read_env_file')

CONFIG_ENV = 'OFFSITE_BACKUP_CONFIG'
#: what ``checks`` answers on a machine where backups are off — the old scripts' convention
OFF = 3

_VARIABLE = re.compile(r'\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}')

logger = logging.getLogger('offsite_backup')


def read_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines: ``#`` comments, an optional ``export``, one pair of surrounding quotes dropped."""
    found: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.removeprefix('export ').split('=', 1)
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        found[key.strip()] = value
    return found


def expand(value: Any, environment: Mapping[str, str]) -> Any:
    """Every ``${NAME}`` / ``${NAME:-default}`` in every string of ``value``, recursively; unset is blank."""
    if isinstance(value, str):

        def one(match: re.Match[str]) -> str:
            if match.group(0) == '$$':
                return '$'
            found = environment.get(match.group(1), '')
            return found if found or match.group(2) is None else match.group(2)

        return _VARIABLE.sub(one, value)
    if isinstance(value, Mapping):
        return {key: expand(item, environment) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item, environment) for item in value]
    return value


def _toml(path: Path) -> dict[str, Any]:
    if sys.version_info >= (3, 11):
        import tomllib  # noqa: PLC0415 - only the command line reads TOML
    else:  # pragma: no cover - Python 3.10
        try:
            import tomli as tomllib  # noqa: PLC0415
        except ImportError as error:
            msg = 'Python 3.10 reads TOML with tomli — pip install "django-offsite-backup[cli]"'
            raise BackupError(msg) from error
    with path.open('rb') as handle:
        return tomllib.load(handle)


def load_config(path: Path, env_files: Sequence[Path] = ()) -> dict[str, Any]:
    """The TOML file, every variable expanded from the environment and the env files."""
    environment = dict(os.environ)
    for env_file in env_files:
        if not env_file.is_file():
            msg = f'env file {str(env_file)!r} not found'
            raise BackupError(msg)
        environment.update(read_env_file(env_file))
    if not path.is_file():
        msg = f'configuration file {str(path)!r} not found'
        raise BackupError(msg)
    try:
        raw = _toml(path)
    except ValueError as error:  # tomllib.TOMLDecodeError
        msg = f'{path}: {error}'
        raise BackupError(msg) from error
    found: dict[str, Any] = expand(raw, environment)
    return found


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='offsite-backup',
        description='Encrypted, immutable off-site backups — the passes of django-offsite-backup, without Django.',
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('--config', type=Path, help=f'The TOML setting (default: ${CONFIG_ENV}).')
    parser.add_argument(
        '--env-file', type=Path, action='append', default=[], help='KEY=value lines the setting reads; repeatable.'
    )
    commands = parser.add_subparsers(dest='command', required=True, metavar='COMMAND')

    run = commands.add_parser('run', help='A pass now.')
    run.add_argument('--without-media', action='store_true', help='Databases and configuration only.')
    run.add_argument('--reason', help='Why it runs: nightly, deploy, upgrade, manual, or any word (DEFAULT_REASON).')
    run.add_argument('--only', action='append', choices=('db', 'media', 'config'), help='Only this part; repeatable.')
    run.add_argument(
        '--path', type=Path, action='append', default=[], help='Add a path to the configuration archive; repeatable.'
    )

    commands.add_parser('check', help='The morning check: say MISSING and fail when no whole backup is recent.')

    status = commands.add_parser('status', help='The newest whole backup per kind; 1 when stale, 2 when unreadable.')
    status.add_argument('--json', action='store_true')
    status.add_argument('--prometheus', metavar='FILE', help='Also write the metrics there (a textfile collector).')
    status.add_argument('--stale-hours', type=float)

    listing = commands.add_parser('list', help='What the store holds, per kind.')
    listing.add_argument('--kind', action='append')
    listing.add_argument('--json', action='store_true')

    restore = commands.add_parser('restore', help='Download, decrypt and verify a stamp; --apply loads a database.')
    restore.add_argument('--stamp', required=True, help='A stamp, a full key with --apply, or "latest".')
    restore.add_argument('--identity', help='The age identity file; default: $AGE_IDENTITY.')
    restore.add_argument('--into', type=Path)
    restore.add_argument('--kind', action='append')
    restore.add_argument('--apply', action='store_true')
    restore.add_argument('--database', help='With --apply: the CONNECTIONS alias to load into.')
    restore.add_argument('--from-database', help='With --apply: whose backup (default: --database).')
    restore.add_argument('--clean', action='store_true', help='With --apply: replace a non-empty database.')

    drill = commands.add_parser('drill', help='Restore the newest whole backup into DRILL["DATABASE"] and report.')
    drill.add_argument('--identity', help='The age identity file; default: DRILL["IDENTITY_FILE"], $AGE_IDENTITY.')
    drill.add_argument('--database', help='The throwaway alias (default: DRILL["DATABASE"]).')
    drill.add_argument('--without-media', action='store_true')

    found = commands.add_parser('checks', help='The system checks; 1 on an error, 3 when backups are off.')
    found.add_argument('--deploy', action='store_true', help='Also the bucket lock, pg_dump against the server, paths.')

    failed = commands.add_parser('failed', help='Say that something before the pass failed, as a FAILED message.')
    failed.add_argument('--reason', help='Why the pass would have run (DEFAULT_REASON).')
    failed.add_argument('text', help='What failed — it is posted, so no secrets.')
    return parser


def _with_paths(values: dict[str, Any], paths: Sequence[Path]) -> dict[str, Any]:
    """``run --path``: the paths added to ``CONFIG``'s, or a ``PathsSource`` of them alone."""
    if not paths:
        return values
    block = dict(values.get('CONFIG') or {'BACKEND': 'offsite_backup.config.paths.PathsSource'})
    listed = block.get('PATHS') or []
    block['PATHS'] = [*([listed] if isinstance(listed, str) else listed), *(str(path) for path in paths)]
    return {**values, 'CONFIG': block}


def _say(text: str) -> None:
    print(text)


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def _run(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup.pass_ import run  # noqa: PLC0415 - imported when a pass runs

    reason = (args.reason or '').strip() or str(settings['DEFAULT_REASON'])
    summary = run(media=not args.without_media, reason=reason, only=args.only)
    _say(f'offsite-backup: {len(summary.uploaded)} objects, {summary.bytes} bytes up')
    return 0


def _check(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup.check import check_last_night  # noqa: PLC0415

    newest = check_last_night()
    _say(f'offsite-backup: ok, newest whole backup {f"{newest:%Y-%m-%d %H:%M} UTC" if newest else "n/a"}')
    return 0


def _status(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup import metrics  # noqa: PLC0415

    code, found = metrics.status(stale_hours=args.stale_hours, textfile=args.prometheus)
    if args.json:
        _say(json.dumps(metrics.as_json(found), indent=2))
    elif not found.up:
        _err(f'offsite-backup: the store could not be read — {found.error}')
    else:
        for line in metrics.lines(found):
            _say(line)
    return code


def _list(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup import listing  # noqa: PLC0415
    from offsite_backup.pass_ import components  # noqa: PLC0415

    table = listing.rows(components(), args.kind or ())
    if args.json:
        _say(json.dumps({'prefix': settings.prefix, 'kinds': table}, indent=2))
    else:
        for line in listing.lines(settings.prefix, table):
            _say(line)
    return 0


def _restore(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup.restore import fetch, restore_database  # noqa: PLC0415

    if args.clean and not args.apply:
        _err('offsite-backup: --clean only means something with --apply')
        return 2
    if args.apply:
        if not args.database:
            _err('offsite-backup: --apply needs --database ALIAS')
            return 2
        key = restore_database(
            args.stamp, args.database, identity=args.identity, clean=args.clean, source_alias=args.from_database
        )
        _say(f'offsite-backup: {key} loaded into {args.database!r}')
        return 0
    into = args.into or Path(f'restore-{args.stamp}')
    for restored in fetch(args.stamp, into=into, identity=args.identity, kinds=args.kind or ()):
        _say(f'{restored.path}' + ('  (verified)' if restored.verified else ''))
    return 0


def _drill(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup.drill import run_drill  # noqa: PLC0415

    report = run_drill(identity=args.identity, database=args.database, media=False if args.without_media else None)
    _say(
        f'offsite-backup: {report.stamp} came back — {len(report.databases)} database(s) '
        f'into {report.target!r}, {len(report.archives)} archive(s) opened'
    )
    return 0


def _failed(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup.notify import notify  # noqa: PLC0415
    from offsite_backup.outcome import FAILED, Outcome  # noqa: PLC0415

    reason = (args.reason or '').strip() or str(settings['DEFAULT_REASON'])
    outcome = Outcome(
        kind=FAILED,
        prefix=settings.prefix,
        env=str(settings['ENV']),
        name=str(settings['NAME']),
        reason=reason,
        error=args.text,
    )
    delivered = notify(outcome, settings)
    _say(f'offsite-backup: failure said through {delivered} notifier(s)')
    return 0


def _checks(args: argparse.Namespace, settings: conf.Settings) -> int:
    from offsite_backup.checks import deploy_findings, settings_findings  # noqa: PLC0415

    found = settings_findings(settings)
    if args.deploy and not any(item.is_serious() for item in found):
        found += deploy_findings(settings)
    for item in found:
        _err(str(item))
    serious = sum(item.is_serious() for item in found)
    _say(f'offsite-backup: {len(found)} finding(s), {serious} error(s)')
    return 1 if serious else 0


_COMMANDS = {
    'run': _run,
    'check': _check,
    'status': _status,
    'list': _list,
    'restore': _restore,
    'drill': _drill,
    'failed': _failed,
    'checks': _checks,
}
#: what may run on a machine where backups are off and say "nothing to do"; the rest refuse
_QUIET_WHEN_OFF = frozenset({'run', 'check', 'status', 'list', 'failed'})


def _off(command: str) -> int:
    """What a command answers on a machine where backups are off."""
    if command == 'checks':
        _say('offsite-backup: not configured on this machine')
        return OFF
    if command in _QUIET_WHEN_OFF:
        _say('offsite-backup: not configured on this machine, nothing to do')
        return 0
    _err('offsite-backup: backups are not configured on this machine')
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Read the setting, run one command, answer its exit code."""
    args = _parser().parse_args(argv)
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s', stream=sys.stderr)
    path = args.config or (Path(os.environ[CONFIG_ENV]) if os.environ.get(CONFIG_ENV) else None)
    if path is None:
        _err(f'offsite-backup: no setting — pass --config FILE or set {CONFIG_ENV}')
        return 2
    try:
        values = _with_paths(load_config(path, args.env_file), getattr(args, 'path', ()))
    except BackupError as error:
        _err(f'offsite-backup: {error}')
        return 2
    conf.configure(values, base_dir=path.resolve().parent)
    try:
        settings = conf.read()
        if not conf.enabled(settings):
            return _off(args.command)
        return _COMMANDS[args.command](args, settings)
    except BackupError as error:
        _err(f'offsite-backup: {error}')
        return 1
    finally:
        conf.configure(None)


def entry() -> None:
    """The console script."""
    sys.exit(main())
