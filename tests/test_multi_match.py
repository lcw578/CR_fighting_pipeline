"""The multi-match supervisor must be bounded, evidence-driven, and fail-closed."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from tools.multi_match import (RESULT_OK_POSITIONS, REMATCH_BUTTON, JsonlLog, MatchSupervisor,
                               SupervisorOptions, classify_probe)

DISMISS, TROPHY_OK, LADDER_OK = RESULT_OK_POSITIONS

RAW_STATES = {
    'lobby': {'in_battle': False},
    'live': {'in_battle': True, 'tick': 100},
    'finalized': {'in_battle': True,
                  'battle_result': {'validated': True, 'finalized': True, 'world_result_raw': 0}},
}


class FakeProbe:
    """Serve scripted raw payloads; hold the last state when exhausted."""

    def __init__(self, states):
        self.states = [RAW_STATES[state] for state in states]
        self._last = self.states[-1] if self.states else {'in_battle': False}

    def query(self):
        if self.states:
            self._last = self.states.pop(0)
        return self._last


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeChild:
    def __init__(self, ai_log, events, returncode=0, raise_timeout=False):
        self.ai_log = ai_log
        self.events = events
        self.returncode = returncode
        self.raise_timeout = raise_timeout
        self.killed = False
        self.waited = 0

    def write_log(self):
        with self.ai_log.open('w', encoding='utf-8') as stream:
            for event in self.events:
                stream.write(json.dumps(event) + '\n')

    def wait(self, timeout):
        self.waited = timeout
        self.write_log()
        if self.raise_timeout:
            raise subprocess.TimeoutExpired('main.py', timeout)
        return self.returncode

    def kill(self):
        self.killed = True


class ScriptedSpawn:
    """Launch one FakeChild per call with scripted outcomes."""

    def __init__(self, per_match):
        self.per_match = list(per_match)
        self.children = []
        self.start_battle_flags = []

    def __call__(self, ai_log, start_battle=True):
        self.start_battle_flags.append(start_battle)
        script = self.per_match[len(self.children)]
        child = FakeChild(ai_log, script.get('events', [{'event': 'battle_start', 'tick': 100}]),
                          returncode=script.get('returncode', 0),
                          raise_timeout=script.get('raise_timeout', False))
        self.children.append(child)
        return child


def make_options(tmp, matches=1, **overrides):
    values = dict(checkpoint='hog26', matches=matches, battle_timeout=600.0, matchmaking_timeout=45.0,
                  live_battle_wait=300.0, result_settle=3.0, lobby_settle=2.0, poll_interval=0.5,
                  result_rounds=3, launch_attempts=2, rematch=False, dry_run=False,
                  output=Path(tmp) / 'multi' / 'session.jsonl')
    values.update(overrides)
    return SupervisorOptions(**values)


def terminal_events(result='win', tick=2500):
    return [{'event': 'battle_start', 'tick': 100},
            {'event': 'battle_terminal', 'tick': tick, 'result': result,
             'crowns': [0, 3] if result == 'win' else [2, 0]}]


def run_supervisor(tmp, options, probe_states, spawn, restart=None):
    clock = FakeClock()
    taps = []
    logger = JsonlLog(options.output)
    supervisor = MatchSupervisor(options, logger=logger, probe=FakeProbe(probe_states),
                                 tapper=lambda x, y: taps.append((x, y)), spawn=spawn,
                                 restart=restart, sleep=clock.sleep, clock=clock.clock)
    code = supervisor.run()
    events = [json.loads(line) for line in options.output.read_text(encoding='utf-8').splitlines()]
    return code, taps, events, supervisor


class ClassifyProbeTests(unittest.TestCase):
    def test_states_are_distinguished(self):
        self.assertEqual(classify_probe(None), 'unreachable')
        self.assertEqual(classify_probe({'in_battle': False}), 'lobby')

    def test_result_requires_validated_and_finalized(self):
        finalized = {'in_battle': True, 'battle_result': {'validated': True, 'finalized': True, 'world_result_raw': 0}}
        self.assertEqual(classify_probe(finalized), 'finalized')
        unvalidated = {'in_battle': True, 'battle_result': {'validated': False, 'finalized': True}}
        self.assertEqual(classify_probe(unvalidated), 'live')
        self.assertEqual(classify_probe({'in_battle': True}), 'live')


class DryRunTests(unittest.TestCase):
    def test_dry_run_records_full_plan_without_devices(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), matches=2, dry_run=True)
            supervisor = MatchSupervisor(options, logger=JsonlLog(options.output))
            code = supervisor.run()
            self.assertEqual(code, 0)
            events = [json.loads(line) for line in options.output.read_text(encoding='utf-8').splitlines()]
            names = [event['event'] for event in events]
            self.assertEqual(names.count('battle_tap'), 2)
            self.assertEqual(names.count('result_tap'), 36)  # 3 rounds x 3 OK positions x 2 phases, per match
            self.assertEqual(names.count('match_bound'), 2)
            self.assertEqual(names[-1], 'run_complete')


class TwoMatchHappyPathTests(unittest.TestCase):
    def test_two_matches_are_ordered_and_dismiss_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            options = make_options(tmp, matches=2)
            # Post-battle the probe keeps serving the frozen terminal snapshot:
            # prepare dismisses it once and launches; only a new battle
            # produces 'live' for the bind.
            states = (['finalized', 'finalized', 'live', 'live']
                      + ['finalized', 'finalized', 'live', 'live'])
            spawn = ScriptedSpawn(per_match=[
                {'events': [{'event': 'battle_start', 'tick': 100},
                            {'event': 'battle_terminal', 'tick': 3000, 'result': 'loss', 'crowns': [2, 0]}]},
                {'events': [{'event': 'battle_start', 'tick': 100},
                            {'event': 'battle_terminal', 'tick': 2800, 'result': 'win', 'crowns': [0, 3]}]},
            ])
            code, taps, events, _ = run_supervisor(tmp, options, states, spawn)
            self.assertEqual(code, 0)
            self.assertEqual(len(spawn.children), 2)
            # Prepare cleanup + 3 dismissal rounds (one tap per OK position),
            # never the screen center.
            per_match = [DISMISS, TROPHY_OK, LADDER_OK] + [DISMISS, TROPHY_OK, LADDER_OK] * 3
            self.assertEqual(taps, per_match * 2)
            names = [event['event'] for event in events]
            self.assertEqual(names.count('match_finished'), 2)
            self.assertEqual(names.count('match_bound'), 2)
            self.assertEqual(names[-1], 'run_complete')
            for number in (1, 2):
                kinds = [event['event'] for event in events if event.get('match') == number]
                self.assertLess(kinds.index('match_bound'), kinds.index('match_finished'))
                self.assertIn('match_complete', kinds)


class RematchFlowTests(unittest.TestCase):
    REMATCH = (370, 1758)

    def test_rematch_skips_lobby_and_taps_play_again(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), matches=2, rematch=True)
            states = (['finalized', 'finalized', 'live', 'live']   # match 1: prepare + bind
                      + ['live', 'live'])                       # match 2: rematch queue binds directly
            spawn = ScriptedSpawn(per_match=[{'events': terminal_events('loss')},
                                             {'events': terminal_events('win')}])
            code, taps, events, _ = run_supervisor(Path(raw), options, states, spawn)
            self.assertEqual(code, 0)
            # Match 1 prepare dismisses once (2 taps); the rematch queue means
            # match 2 prepare reads 'live' immediately (no dismiss taps), and
            # match 2 still taps rematch for the next match at its end.
            self.assertEqual(taps.count(REMATCH_BUTTON), 2)
            self.assertEqual(taps.count(DISMISS), 1)
            self.assertEqual(taps.count(TROPHY_OK), 1)
            # Match 1 child uses --start-battle (lobby launch); match 2 does not.
            self.assertEqual(spawn.start_battle_flags, [True, False])
            names = [event['event'] for event in events]
            self.assertEqual(names.count('rematch_tap'), 2)
            self.assertEqual(names.count('match_complete'), 2)
            self.assertEqual(names[-1], 'run_complete')

    def test_remiss_falls_back_to_lobby_battle_button(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), matches=2, rematch=True,
                                   matchmaking_timeout=2.0, live_battle_wait=10.0)
            # Match 2: the rematch tap missed (single-OK layout keeps serving
            # the frozen terminal snapshot); attempt 1 times out waiting for
            # the queue, the dismissal round falls back to the lobby flow, and
            # attempt 2 runs with --start-battle and binds.
            states = (['finalized', 'finalized', 'live', 'live']
                      + ['finalized'] * 5 + ['live', 'live'])
            spawn = ScriptedSpawn(per_match=[{'events': terminal_events('loss')},
                                             {},
                                             {'events': terminal_events('win')}])
            code, taps, events, _ = run_supervisor(Path(raw), options, states, spawn)
            self.assertEqual(code, 0)
            # The attempt-2 rematch retry tap succeeds (the button is
            # interactive by then), so the lobby fallback (attempt 3) is not
            # reached in this scenario.
            self.assertEqual(spawn.start_battle_flags, [True, False, False])
            names = [event['event'] for event in events]
            self.assertEqual(names.count('launch_retry'), 1)
            self.assertEqual(names.count('bind_attempt_failed'), 1)
            # 3 rematch taps: match 1 end, match 2 retry, match 2 end.
            self.assertEqual(names.count('rematch_tap'), 3)
            self.assertEqual(names.count('match_complete'), 2)
            self.assertEqual(names[-1], 'run_complete')


class FailClosedTests(unittest.TestCase):
    def test_orphan_live_battle_is_waited_out_then_launched(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw))
            # A live battle nobody is driving appears before launch and ends
            # during the bounded wait; the session proceeds once it is gone.
            spawn = ScriptedSpawn(per_match=[{'events': [
                {'event': 'battle_start', 'tick': 100},
                {'event': 'battle_terminal', 'tick': 2500, 'result': 'win', 'crowns': [0, 3]}]}])
            dismiss, trophy, ladder = RESULT_OK_POSITIONS
            code, taps, events, _ = run_supervisor(Path(raw), options, ['live', 'live', 'lobby', 'live', 'live'], spawn)
            self.assertEqual(code, 0)
            self.assertEqual(len(spawn.children), 1)
            self.assertEqual(taps, [dismiss, trophy, ladder] * 3)
            self.assertNotIn('unexpected_live_battle', [e.get('reason') for e in events
                if e.get('event') == 'match_failed'])

    def test_persistent_live_battle_fails_after_bounded_wait(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), live_battle_wait=2.0)
            spawn = ScriptedSpawn(per_match=[{}])
            code, taps, events, _ = run_supervisor(Path(raw), options, ['live', 'live'], spawn)
            self.assertEqual(code, 2)
            self.assertEqual(taps, [])
            self.assertEqual(spawn.children, [])
            failures = [event for event in events if event['event'] == 'match_failed']
            self.assertEqual(failures[-1]['reason'], 'prepare_timeout')

    def test_stale_live_battle_restarts_game_then_proceeds(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), live_battle_wait=2.0)
            # The orphan battle never finalizes; one game restart clears the
            # stalemate and the next lobby read lets the session proceed.
            spawn = ScriptedSpawn(per_match=[{'events': [
                {'event': 'battle_start', 'tick': 100},
                {'event': 'battle_terminal', 'tick': 2500, 'result': 'win', 'crowns': [0, 3]}]}])
            restarts = []
            code, taps, events, _ = run_supervisor(Path(raw), options,
                ['live', 'live', 'live', 'live', 'lobby', 'live'], spawn,
                restart=lambda: restarts.append(1))
            self.assertEqual(code, 0)
            self.assertEqual(len(restarts), 1)
            self.assertEqual(len(spawn.children), 1)
            names = [event['event'] for event in events]
            self.assertIn('stale_battle_restart', names)
            self.assertEqual(names[-1], 'run_complete')

    def test_stale_live_battle_persists_after_one_restart_then_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), live_battle_wait=2.0)
            spawn = ScriptedSpawn(per_match=[{}])
            restarts = []
            code, taps, events, _ = run_supervisor(Path(raw), options, ['live'], spawn,
                restart=lambda: restarts.append(1))
            self.assertEqual(code, 2)
            self.assertEqual(len(restarts), 1)
            self.assertEqual(spawn.children, [])
            failures = [event for event in events if event['event'] == 'match_failed']
            self.assertEqual(failures[-1]['reason'], 'prepare_timeout')

    def test_battle_tap_without_effect_clears_and_retries_before_failing(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), matches=1, matchmaking_timeout=2.0, live_battle_wait=10.0)
            # Prepare dismisses the stale panel, but both launch attempts still
            # fail to bind (the frozen snapshot never clears into a new battle).
            spawn = ScriptedSpawn(per_match=[{}, {}])
            code, taps, events, _ = run_supervisor(Path(raw), options,
                ['finalized', 'lobby', 'finalized', 'finalized', 'finalized', 'finalized', 'finalized'], spawn)
            self.assertEqual(code, 2)
            self.assertEqual(len(spawn.children), 2)
            self.assertTrue(all(child.killed for child in spawn.children))
            failures = [event for event in events if event['event'] == 'match_failed']
            self.assertEqual(failures[-1]['reason'], 'battle_tap_no_effect')
            retries = [event for event in events if event['event'] == 'launch_retry']
            self.assertEqual(len(retries), 1)

    def test_queued_result_pages_are_cleared_then_second_launch_binds(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), matchmaking_timeout=2.0)
            # Attempt 1 binds during a page queue and times out; the dismissal
            # round clears the pages and attempt 2 binds on the fresh battle.
            states = ['finalized', 'lobby'] + ['finalized'] * 4 + ['live']
            spawn = ScriptedSpawn(per_match=[{}, {
                'events': [{'event': 'battle_start', 'tick': 100},
                           {'event': 'battle_terminal', 'tick': 2500, 'result': 'win', 'crowns': [0, 1]}]}])
            code, taps, events, _ = run_supervisor(Path(raw), options, states, spawn)
            self.assertEqual(code, 0)
            self.assertTrue(spawn.children[0].killed)
            self.assertFalse(spawn.children[1].killed)
            names = [event['event'] for event in events]
            self.assertIn('launch_retry', names)
            self.assertEqual(names.count('match_finished'), 1)
            self.assertEqual(names[-1], 'run_complete')

    def test_missing_terminal_evidence_stops_the_session(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), matches=2)
            states = ['finalized', 'lobby', 'live']  # first match only
            spawn = ScriptedSpawn(per_match=[{'events': [{'event': 'battle_start', 'tick': 100}]}])
            code, taps, events, _ = run_supervisor(Path(raw), options, states, spawn)
            self.assertEqual(code, 2)
            self.assertEqual(len(spawn.children), 1)
            failures = [event for event in events if event['event'] == 'match_failed']
            self.assertEqual(failures[-1]['reason'], 'missing_battle_terminal')

    def test_child_battle_timeout_is_killed_and_stops_the_session(self):
        with tempfile.TemporaryDirectory() as raw:
            options = make_options(Path(raw), matches=2)
            spawn = ScriptedSpawn(per_match=[{'raise_timeout': True}])
            code, taps, events, _ = run_supervisor(Path(raw), options, ['finalized', 'lobby', 'live'], spawn)
            self.assertEqual(code, 2)
            self.assertTrue(spawn.children[0].killed)
            self.assertEqual(len(spawn.children), 1)
            failures = [event for event in events if event['event'] == 'match_failed']
            self.assertEqual(failures[-1]['reason'], 'battle_timeout')


class OptionsValidationTests(unittest.TestCase):
    def test_matches_must_be_positive(self):
        from tools.multi_match import options_from_args
        with tempfile.TemporaryDirectory() as raw:
            args = type('Args', (), {'checkpoint': 'hog26', 'matches': 0, 'battle_timeout': 600.0,
                                     'matchmaking_timeout': 120.0, 'result_settle': 3.0, 'lobby_settle': 2.0,
                                     'poll_interval': 0.5, 'dry_run': True, 'output': Path(raw) / 'x.jsonl'})
            with self.assertRaises(ValueError):
                options_from_args(args)


if __name__ == '__main__':
    unittest.main()
