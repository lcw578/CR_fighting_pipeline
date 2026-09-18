"""The emote capture tool and the shipped emote cadence must stay usable.

The cadence in ``settings.example.json`` is the public default template, so it
has to keep loading through ``config`` and keep matching the documented values.
The capture tool must never need a host-specific constant of its own.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import capture_emotes

ROOT = Path(__file__).resolve().parents[1]

# The cadence the example template ships: the measured sweet spot for long runs.
EXPECTED_CADENCE = {
    'emote_tray_open_seconds': 0.12,
    'emote_first_delay_seconds': 15.0,
    'emote_min_interval_seconds': 3.5,
    'emote_max_interval_seconds': 5.0,
    'emote_ability_hud_margin': 24.0,
}


class ThumbnailSizeTests(unittest.TestCase):
    def test_size_parses_both_dimensions(self):
        self.assertEqual(capture_emotes.parse_size('540x960'), (540, 960))
        self.assertEqual(capture_emotes.parse_size('1080X1920'), (1080, 1920))

    def test_malformed_sizes_are_refused(self):
        for value in ('540', '540x', 'axb', '540x0', '-1x100'):
            with self.subTest(value=value):
                with self.assertRaises(argparse.ArgumentTypeError):
                    capture_emotes.parse_size(value)


class CaptureOptionTests(unittest.TestCase):
    def test_log_and_output_are_required(self):
        with self.assertRaises(SystemExit):
            capture_emotes.build_parser().parse_args([])

    def test_defaults_match_the_documented_values(self):
        options = capture_emotes.build_parser().parse_args(
            ['--log', 'a.jsonl', '--output', 'out'])
        self.assertEqual(options.idle, capture_emotes.DEFAULT_IDLE)
        self.assertEqual(options.thumbnail, capture_emotes.DEFAULT_THUMBNAIL)
        self.assertIsNone(options.adb)
        self.assertIsNone(options.serial)

    def test_missing_serial_and_bad_idle_stop_before_touching_adb(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings = Path(tmp) / 'settings.json'
            settings.write_text(json.dumps({'adb_serial': '', 'probe_port': 26888}))
            env = dict(os.environ, CR_AGENT_SETTINGS=str(settings))
            command = [sys.executable, '-X', 'utf8', str(ROOT / 'tools' / 'capture_emotes.py'),
                       '--log', str(Path(tmp) / 'a.jsonl'), '--output', str(Path(tmp) / 'out')]
            no_serial = subprocess.run(command + ['--idle', '1'], cwd=ROOT, env=env,
                                       capture_output=True, text=True)
            self.assertEqual(no_serial.returncode, 2)
            self.assertIn('No ADB serial configured', no_serial.stdout)

            bad_idle = subprocess.run(command + ['--idle', '0', '--serial', '127.0.0.1:16384'],
                                      cwd=ROOT, env=env, capture_output=True, text=True)
            self.assertEqual(bad_idle.returncode, 2)
            self.assertIn('--idle must be positive', bad_idle.stdout)

    def test_the_tool_carries_no_host_specific_constant(self):
        source = (ROOT / 'tools' / 'capture_emotes.py').read_text(encoding='utf-8')
        for forbidden in ('D:/', 'C:/', 'MuMu Player', '127.0.0.1:16384', 'E:/'):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class ShippedCadenceTests(unittest.TestCase):
    def test_the_example_template_carries_the_long_run_cadence(self):
        example = json.loads((ROOT / 'settings.example.json').read_text(encoding='utf-8'))
        for key, expected in EXPECTED_CADENCE.items():
            with self.subTest(key=key):
                self.assertEqual(example[key], expected)

    def test_the_example_template_loads_through_config(self):
        output = subprocess.check_output(
            [sys.executable, '-c',
             'import config,json; print(json.dumps({' + ','.join(
                 f'"emote_{key}": config.EMOTE_{key.upper()}' for key in
                 ('tray_open_seconds', 'first_delay_seconds', 'min_interval_seconds',
                  'max_interval_seconds', 'ability_hud_margin')) + '}))'],
            cwd=ROOT,
            env=dict(os.environ, CR_AGENT_SETTINGS=str(ROOT / 'settings.example.json')),
            text=True)
        self.assertEqual(json.loads(output), EXPECTED_CADENCE)

    def test_the_cadence_is_a_template_not_an_implicit_switch(self):
        # Shipping the interval must not start sending: --emote still gates it.
        import main as agent_main
        self.assertIs(agent_main.parse_args([]).emote, False)
        self.assertIs(agent_main.parse_args(['--emote']).emote, True)


if __name__ == '__main__':
    unittest.main()
