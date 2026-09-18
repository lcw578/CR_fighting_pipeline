"""Run bounded match sessions back to back, unattended, for as long as you want.

``tools/multi_match.py`` deliberately bounds ``--matches``: it is the only thing
that touches ADB, and it stops the whole session the moment a stage times out
instead of retrying into a broken emulator.  That is the right behaviour for a
supervised run, but it means a long night of matches needs something on top.

This wrapper provides exactly that, and nothing else: it starts another
``multi_match.py`` session when the previous one finishes, so the bounded tool
stays the only process that drives the game.

Three guards make an endless run safe to leave alone:

* **log rotation** -- one match writes 70-260 MB of JSONL.  Only the newest
  ``--keep-logs`` per-match logs are retained; older ones are deleted.
* **sentinel stop** -- creating the stop file ends the loop within a few
  seconds, including in the middle of a session.  The default location keeps
  the documented ``local/STOP_FOREVER`` workflow working.
* **backoff** -- consecutive sessions that complete no match at all end the run
  instead of hammering a broken emulator forever.

Everything machine-specific is a command-line option; this file contains no
host paths, no account identity and no emulator address of its own.

Typical use, with automatic emotes on the long-run cadence::

    .venv\\Scripts\\python.exe tools\\forever.py --checkpoint hog26

Stop it by creating the stop file (``local/STOP_FOREVER`` by default) or by
pressing Ctrl+C.  ``--dry-run`` prints the resolved plan without starting
anything.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / 'logs' / 'forever'
DEFAULT_STOP_FILE = ROOT / 'local' / 'STOP_FOREVER'
RUNNER_LOG_NAME = 'runner.log'


def note(stream_path: Path, message: str) -> None:
    """Echo to the console and append to the runner log."""

    line = f'{datetime.now().isoformat(timespec="seconds")}  {message}'
    print(line, flush=True)
    stream_path.parent.mkdir(parents=True, exist_ok=True)
    with stream_path.open('a', encoding='utf-8') as stream:
        stream.write(line + '\n')


def rotate_logs(output_dir: Path, keep: int) -> int:
    """Keep only the newest ``keep`` per-match logs; return how many went."""

    logs = sorted(output_dir.glob('*_match*.jsonl'), key=lambda path: path.stat().st_mtime)
    removed = 0
    for path in logs[:-keep] if keep else logs:
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def completed_matches(session_log: Path) -> int:
    """Count the finished matches a session recorded before it exited."""

    if not session_log.is_file():
        return 0
    count = 0
    for line in session_log.read_text(encoding='utf-8', errors='replace').splitlines():
        if '"match_complete"' in line:
            count += 1
    return count


def terminate_tree(process: subprocess.Popen) -> None:
    """Stop the session and every child it started."""

    subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)],
                   capture_output=True, check=False)


def session_command(options: argparse.Namespace, session_log: Path) -> list[str]:
    command = [sys.executable, '-X', 'utf8', '-u', str(ROOT / 'tools' / 'multi_match.py'),
               '--matches', str(options.matches_per_session),
               '--checkpoint', options.checkpoint,
               '--output', str(session_log)]
    if options.emote:
        command.append('--emote')
    if options.rematch:
        command.append('--rematch')
    return command


def run_session(options: argparse.Namespace, index: int, runner_log: Path) -> int:
    """Run one bounded session.  Returns matches completed, or -1 if stopped."""

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    session_log = options.output_dir / f'session_{stamp}_{index:04d}.jsonl'
    if options.stop_file.exists():
        return -1

    command = session_command(options, session_log)
    note(runner_log, f'session {index}: {options.matches_per_session} matches -> {session_log.name}')
    env = dict(os.environ)
    if options.settings:
        env['CR_AGENT_SETTINGS'] = str(options.settings)
    process = subprocess.Popen(command, cwd=ROOT, env=env,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    while process.poll() is None:
        if options.stop_file.exists():
            note(runner_log, 'stop requested: terminating the running session')
            terminate_tree(process)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                note(runner_log, 'the session ignored the stop request; giving up on it')
            return -1
        time.sleep(options.poll_seconds)
    return completed_matches(session_log)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--checkpoint', default='hog26',
                        help='Model alias passed to multi_match.py (default: hog26)')
    parser.add_argument('--matches-per-session', type=int, default=10,
                        help='Matches per supervised session (default: 10)')
    parser.add_argument('--keep-logs', type=int, default=40,
                        help='Newest per-match logs retained; older ones are deleted (default: 40)')
    parser.add_argument('--max-bad-sessions', type=int, default=3,
                        help='Consecutive matchless sessions before the run stops (default: 3)')
    parser.add_argument('--retry-backoff', type=float, default=60.0,
                        help='Seconds multiplied by the bad-session count before retrying (default: 60)')
    parser.add_argument('--poll-seconds', type=float, default=5.0,
                        help='How often to check for the stop file (default: 5)')
    parser.add_argument('--output-dir', type=Path, default=DEFAULT_OUTPUT_DIR,
                        help='Where session logs and runner.log are written')
    parser.add_argument('--stop-file', type=Path, default=DEFAULT_STOP_FILE,
                        help='Creating this file stops the loop (default: local/STOP_FOREVER)')
    parser.add_argument('--settings', type=Path, default=None,
                        help='Settings JSON for the child, via CR_AGENT_SETTINGS '
                             '(default: whatever the current environment already selects)')
    parser.add_argument('--no-emote', dest='emote', action='store_false',
                        help='Do not send emotes; by default the long run sends them')
    parser.add_argument('--rematch', action='store_true',
                        help='Use the ladder "play again" button to chain matches')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print the resolved plan without starting any session')
    return parser


def validate(options: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if options.matches_per_session < 1:
        parser.error('--matches-per-session must be positive')
    if options.keep_logs < 0:
        parser.error('--keep-logs cannot be negative')
    if options.max_bad_sessions < 1:
        parser.error('--max-bad-sessions must be positive')
    if options.retry_backoff < 0 or options.poll_seconds <= 0:
        parser.error('--retry-backoff cannot be negative and --poll-seconds must be positive')
    if options.settings and not options.settings.is_file():
        parser.error(f'--settings does not exist: {options.settings}')


def main() -> int:
    parser = build_parser()
    options = parser.parse_args()
    validate(options, parser)
    options.output_dir = options.output_dir.resolve()
    options.stop_file = options.stop_file.resolve()
    if options.settings:
        options.settings = options.settings.resolve()

    runner_log = options.output_dir / RUNNER_LOG_NAME
    plan = {
        'checkpoint': options.checkpoint,
        'matchesPerSession': options.matches_per_session,
        'keepLogs': options.keep_logs,
        'maxBadSessions': options.max_bad_sessions,
        'emote': options.emote,
        'rematch': options.rematch,
        'outputDir': str(options.output_dir),
        'stopFile': str(options.stop_file),
        'settings': str(options.settings) if options.settings else None,
    }
    if options.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2), flush=True)
        print('\ndry run: no session was started', flush=True)
        return 0

    if options.stop_file.exists():
        options.stop_file.unlink()
    options.output_dir.mkdir(parents=True, exist_ok=True)
    note(runner_log, 'runner start: ' + json.dumps(plan, ensure_ascii=False))

    bad = 0
    index = 0
    while True:
        index += 1
        rotated = rotate_logs(options.output_dir, options.keep_logs)
        if rotated:
            note(runner_log, f'rotated {rotated} old match log(s)')
        played = run_session(options, index, runner_log)
        if played < 0:
            note(runner_log, 'runner stopped by sentinel')
            return 0
        note(runner_log, f'session {index} finished: {played} match(es) completed')
        if played == 0:
            bad += 1
            if bad >= options.max_bad_sessions:
                note(runner_log, f'{bad} consecutive sessions completed no match; stopping')
                return 3
            delay = options.retry_backoff * bad
            note(runner_log, f'no match completed; backing off {delay:.0f}s '
                             f'({bad}/{options.max_bad_sessions})')
            time.sleep(delay)
        else:
            bad = 0
        time.sleep(options.poll_seconds)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('interrupted', flush=True)
        raise SystemExit(130)
