"""The unattended wrapper must rotate, stop on the sentinel, and bound retries.

These tests cover the three guards and the "everything host-specific is an
option" contract, without touching ADB, the probe, or a real session.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.forever import (DEFAULT_STOP_FILE, ROOT, build_parser, completed_matches,
                           rotate_logs, session_command)


def parse(*argv):
    return build_parser().parse_args(list(argv))


def touch(path, body=''):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding='utf-8')
    return path


class LogRotationTests(unittest.TestCase):
    def test_only_the_newest_logs_survive(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for index in range(6):
                path = touch(out / f'session_1_match{index}.jsonl', '{}\n')
                # Distinct, increasing mtimes so "newest" is unambiguous.
                path.touch()
            removed = rotate_logs(out, 2)
            self.assertEqual(removed, 4)
            kept = sorted(p.name for p in out.glob('*_match*.jsonl'))
            self.assertEqual(len(kept), 2)

    def test_zero_retention_deletes_every_per_match_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            touch(out / 'session_1_match1.jsonl', '{}\n')
            touch(out / 'session_1_match2.jsonl', '{}\n')
            self.assertEqual(rotate_logs(out, 0), 2)
            self.assertEqual(list(out.glob('*_match*.jsonl')), [])

    def test_session_logs_are_not_rotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            touch(out / 'session_20260918-000000_0001.jsonl', '{}\n')
            touch(out / 'session_20260918-000000_0001_match1.jsonl', '{}\n')
            rotate_logs(out, 0)
            self.assertEqual([p.name for p in out.glob('*.jsonl')],
                             ['session_20260918-000000_0001.jsonl'])


class CompletedMatchTests(unittest.TestCase):
    def test_counts_only_match_complete_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = touch(Path(tmp) / 'session.jsonl',
                        '{"event":"state_enter"}\n'
                        '{"event":"match_complete","result":"win"}\n'
                        '{"event":"match_complete","result":"loss"}\n')
            self.assertEqual(completed_matches(log), 2)

    def test_a_missing_session_log_counts_as_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(completed_matches(Path(tmp) / 'absent.jsonl'), 0)


class OptionContractTests(unittest.TestCase):
    def test_defaults_send_emotes_and_bound_the_retries(self):
        options = parse()
        self.assertTrue(options.emote)
        self.assertEqual(options.matches_per_session, 10)
        self.assertEqual(options.keep_logs, 40)
        self.assertEqual(options.max_bad_sessions, 3)
        self.assertEqual(options.checkpoint, 'hog26')

    def test_no_emote_disables_the_tray_without_touching_anything_else(self):
        options = parse('--no-emote')
        self.assertFalse(options.emote)
        self.assertNotIn('--emote', session_command(options, Path('x.jsonl')))

    def test_the_stop_file_defaults_to_the_documented_local_path(self):
        self.assertEqual(parse().stop_file, DEFAULT_STOP_FILE)
        self.assertEqual(DEFAULT_STOP_FILE, ROOT / 'local' / 'STOP_FOREVER')

    def test_the_child_command_carries_the_bounded_options(self):
        options = parse('--checkpoint', 'active_il', '--matches-per-session', '4', '--rematch')
        command = session_command(options, Path('session.jsonl'))
        self.assertIn('--emote', command)
        self.assertIn('--rematch', command)
        self.assertEqual(command[command.index('--matches') + 1], '4')
        self.assertEqual(command[command.index('--checkpoint') + 1], 'active_il')
        self.assertEqual(command[command.index('--output') + 1], 'session.jsonl')
        self.assertTrue(any(part.endswith('multi_match.py') for part in command))

    def test_invalid_numbers_are_refused(self):
        for argv in (['--matches-per-session', '0'], ['--keep-logs', '-1'],
                     ['--max-bad-sessions', '0'], ['--poll-seconds', '0'],
                     ['--retry-backoff', '-5']):
            with self.subTest(argv=argv):
                options = parse(*argv)
                with self.assertRaises(SystemExit):
                    from tools.forever import validate
                    validate(options, _ErrorParser())

    def test_a_missing_settings_file_is_refused(self):
        from tools.forever import validate
        options = parse('--settings', str(Path(tempfile.gettempdir()) / 'cr-absent.json'))
        with self.assertRaises(SystemExit):
            validate(options, _ErrorParser())


class _ErrorParser:
    """Stand-in for argparse's parser: ``error`` must abort, as it does in main."""

    def error(self, message):
        raise SystemExit(message)


class DryRunTests(unittest.TestCase):
    def test_dry_run_prints_the_plan_and_starts_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'forever'
            stop = Path(tmp) / 'STOP'
            result = subprocess.run(
                [sys.executable, '-X', 'utf8', str(ROOT / 'tools' / 'forever.py'),
                 '--dry-run', '--output-dir', str(out), '--stop-file', str(stop)],
                cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(result.stdout[:result.stdout.index('}\n') + 1])
            self.assertEqual(plan['matchesPerSession'], 10)
            self.assertTrue(plan['emote'])
            # A dry run must not create the output directory or the stop file.
            self.assertFalse(out.exists())
            self.assertFalse(stop.exists())


if __name__ == '__main__':
    unittest.main()
