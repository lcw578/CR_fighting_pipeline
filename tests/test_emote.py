import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import config
if str(config.FIRSTLIGHT_DIR) not in sys.path:
    sys.path.insert(0, str(config.FIRSTLIGHT_DIR))

from bridge.actuator import Actuator
from bridge.coordinates import ScreenCalibration
from bridge.emote import EmoteError, EmoteScheduler, emote_allowed, emote_points
from bridge.probe_client import ProbeClient
from agent.execution import ActionExecutor
from agent.feature_adapter import FeatureAdapter
from main import CustomCardDeployAgent
from native_runner.contracts import ActionV1, ActionKind
# Imported through the package: agent.policy_engine inserts the project root at
# sys.path[0] on import, which would otherwise shadow this module with the
# same-named runner script at the repository root.
from tests.test_pipeline import opening


CALIBRATION = {
    'size': [1080, 1920],
    'emote_button': [106, 1640],
    'emote_slots': [[280, 1265], [475, 1265], [672, 1265],
                    [280, 1438], [475, 1438], [672, 1438]],
    'verified': True,
}


def idle_executor():
    """The three attributes ``emote_allowed`` reads off a live ActionExecutor."""
    return SimpleNamespace(future=None, pending=[], fault=None)


class EmotePointsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def write(self, **changes):
        path = Path(self.directory.name) / 'emote.json'
        path.write_text(json.dumps({**CALIBRATION, **changes}), encoding='utf-8')
        return path

    def test_verified_calibration_scales_to_the_touch_size(self):
        button, slots = emote_points(self.write(), (1080, 1920))
        self.assertEqual(button, (106, 1640))
        self.assertEqual(len(slots), 6)
        self.assertEqual(slots[0], (280, 1265))
        button, slots = emote_points(self.write(), (2160, 3840))
        self.assertEqual(button, (212, 3280))
        self.assertEqual(slots[0], (560, 2530))

    def test_unverified_calibration_is_rejected(self):
        with self.assertRaises(EmoteError):
            emote_points(self.write(verified=False), (1080, 1920))

    def test_missing_file_is_rejected(self):
        with self.assertRaises(OSError):
            emote_points(Path(self.directory.name) / 'absent.json', (1080, 1920))

    def test_button_outside_screen_is_rejected(self):
        with self.assertRaises(EmoteError):
            emote_points(self.write(emote_button=[1200, 1640]), (1080, 1920))

    def test_empty_slot_list_is_rejected(self):
        with self.assertRaises(EmoteError):
            emote_points(self.write(emote_slots=[]), (1080, 1920))

    def test_malformed_slot_is_rejected(self):
        for slots in ([[280], [475, 1265]], [[280, 1265, 30]], [['a', 1265]]):
            with self.assertRaises(EmoteError):
                emote_points(self.write(emote_slots=slots), (1080, 1920))

    def test_aspect_ratio_change_is_rejected(self):
        with self.assertRaises(EmoteError):
            emote_points(self.write(), (720, 1920))


class EmoteSchedulerTests(unittest.TestCase):
    def make(self, **overrides):
        values = dict(button=(106, 1640), slots=((280, 1265), (475, 1265)),
                      first_delay=15.0, min_interval=20.0, max_interval=35.0, now=100.0)
        values.update(overrides)
        return EmoteScheduler(**values)

    def test_first_emote_waits_for_the_opening_delay(self):
        scheduler = self.make()
        self.assertFalse(scheduler.due(114.999))
        self.assertTrue(scheduler.due(115.0))

    def test_immediate_first_delay_is_due_at_once(self):
        self.assertTrue(self.make(first_delay=0.0).due(100.0))

    def test_reschedule_stays_inside_the_configured_window(self):
        scheduler = self.make(first_delay=0.0)
        for _ in range(200):
            scheduler.reschedule(1000.0)
            self.assertGreaterEqual(scheduler.next_at - 1000.0, 20.0)
            self.assertLessEqual(scheduler.next_at - 1000.0, 35.0)

    def test_pick_stays_inside_the_slot_list(self):
        scheduler = self.make()
        self.assertEqual({scheduler.pick() for _ in range(200)}, {0, 1})

    def test_invalid_intervals_are_rejected(self):
        for overrides in ({'min_interval': 35.0, 'max_interval': 20.0}, {'min_interval': 0.0},
                          {'first_delay': -1.0}, {'slots': ()}):
            with self.assertRaises(EmoteError):
                self.make(**overrides)


class EmoteAllowedTests(unittest.TestCase):
    def setUp(self):
        self.scheduler = EmoteScheduler((106, 1640), ((280, 1265),), first_delay=0.0,
                                        min_interval=20.0, max_interval=35.0, now=0.0)

    def test_idle_executor_is_allowed(self):
        self.assertTrue(emote_allowed(self.scheduler, idle_executor(), 1.0))

    def test_missing_scheduler_is_never_allowed(self):
        self.assertFalse(emote_allowed(None, idle_executor(), 1.0))

    def test_scheduler_is_not_due_yet(self):
        self.scheduler.next_at = 50.0
        self.assertFalse(emote_allowed(self.scheduler, idle_executor(), 1.0))

    def test_queued_action_blocks_the_emote(self):
        executor = idle_executor()
        executor.pending = [object()]
        self.assertFalse(emote_allowed(self.scheduler, executor, 1.0))

    def test_action_in_flight_blocks_the_emote(self):
        executor = idle_executor()
        executor.future = object()
        self.assertFalse(emote_allowed(self.scheduler, executor, 1.0))

    def test_input_fault_blocks_the_emote(self):
        executor = idle_executor()
        executor.fault = 'ADB command timeout'
        self.assertFalse(emote_allowed(self.scheduler, executor, 1.0))


class ActuatorEmoteTests(unittest.TestCase):
    def setUp(self):
        # Placeholder target: this test never opens ADB, it only records the
        # command the actuator would send, so no real instance belongs here.
        self.actuator = Actuator(adb_path='adb', serial='test-serial')
        self.actuator.size = (1080, 1920)
        self.commands = []
        self.actuator._command = self._record

    def _record(self, command):
        self.commands.append(command)
        return .012

    def test_emote_is_one_command_with_the_tray_opened_first(self):
        receipt = self.actuator.emote((106, 1640), (280, 1265))
        self.assertEqual(len(self.commands), 1)
        command = self.commands[0]
        self.assertIn('input tap 106 1640', command)
        self.assertIn('input tap 280 1265', command)
        self.assertLess(command.index('input tap 106 1640'), command.index('input tap 280 1265'))
        self.assertIn('sleep', command)
        self.assertEqual(receipt['button'], [106, 1640])
        self.assertEqual(receipt['slot'], [280, 1265])
        self.assertAlmostEqual(receipt['input_ms'], 12.0)

    def test_slot_hosting_the_hero_skill_hud_is_refused(self):
        with self.assertRaises(ValueError):
            self.actuator.emote((106, 1640), (900, 1450))
        self.assertEqual(self.commands, [])

    def test_upstream_fourth_column_is_refused_by_the_hud_margin(self):
        # The reference tray also lists x=869, which is 6 px clear of
        # ABILITY_HUD_BOUNDS; the emote margin must still reject it.
        with self.assertRaises(ValueError):
            self.actuator.emote((106, 1640), (869, 1438))
        self.assertEqual(self.commands, [])

    def test_tray_button_is_never_checked_against_the_arena(self):
        # The button sits in the bottom-left HUD, outside the ground bounds.
        self.actuator.emote((106, 1640), (280, 1438))
        self.assertEqual(len(self.commands), 1)


class EmoteLoopTests(unittest.TestCase):
    """The live loop must emote only while idle and never replay a tap."""

    def setUp(self):
        # The live loop refuses to run with unverified ground coordinates. Pin
        # them here so the suite does not depend on this machine having a
        # settings.local.json with calibration_verified=true.
        verified = patch.object(config, 'CALIBRATION_VERIFIED', True)
        verified.start()
        self.addCleanup(verified.stop)

    def build(self, events, ticks, *, dry_run, emote_side_effect=None):
        parser = ProbeClient(account_id=123)
        states = []
        for tick in ticks:
            raw = opening()
            raw['tick'] = tick
            states.append(parser.parse(copy.deepcopy(raw)))
        clock = [100.0]
        sequence = iter(states)
        remaining = [len(states)]

        agent = CustomCardDeployAgent.__new__(CustomCardDeployAgent)
        agent.emote_enabled = True
        agent.log = lambda event, **data: events.append((event, data))
        agent.probe = Mock(known_account_id=123, last_status='live', last_error=None, last_query_ms=0)

        def next_state():
            clock[0] += .3
            value = next(sequence, None)
            remaining[0] -= 1
            if remaining[0] <= 0:  # the loop finishes the state it just fetched
                agent.running = False
            return value

        agent.probe.get_live_battle_state.side_effect = next_state
        agent.adapter = FeatureAdapter()
        agent.actuator = Mock(calibration=ScreenCalibration(), size=(1080, 1920))
        agent.actuator.emote.return_value = {'button': [106, 1640], 'slot': [280, 1265], 'input_ms': 12.0}
        if emote_side_effect is not None:
            agent.actuator.emote.side_effect = emote_side_effect
        agent.executor = ActionExecutor(agent.actuator, agent.log, dry_run=dry_run)
        agent.engine = Mock(warmed_up=True, checkpoint_path=Path('test.pt'), meta={}, last_timings={})
        agent.engine.decide.return_value = (SimpleNamespace(actions=(ActionV1(owner=0, kind=ActionKind.WAIT),)), 1)
        agent.stream = io.StringIO()
        agent.log_path = Path('unused-test-log')
        agent.dry_run = dry_run
        return agent, clock

    def test_dry_run_fills_an_idle_window_and_logs_the_tray_points(self):
        events = []
        agent, clock = self.build(events, (90, 95, 100), dry_run=True)
        points = ((106, 1640), ((280, 1265), (475, 1265)))
        with patch('main.emote_points', return_value=points), \
                patch.object(config, 'EMOTE_FIRST_DELAY_SECONDS', 0.0), \
                patch('main.time.perf_counter', side_effect=lambda: clock[0]), patch('main.time.sleep'):
            agent.run(once=True)
        self.assertEqual([d['tick'] for e, d in events if e == 'decision'], [90, 95, 100])
        emotes = [d for e, d in events if e == 'dry_run_emote']
        self.assertEqual(len(emotes), 1)
        self.assertEqual(emotes[0]['button'], [106, 1640])
        self.assertIn(emotes[0]['slot'], ([280, 1265], [475, 1265]))
        self.assertNotIn('emote_input_disabled', [e for e, _ in events])
        agent.executor.close()

    def test_no_emote_during_the_pre_battle_transition(self):
        # Ticks below the first playable tick are the loading/chest screen,
        # where the emote tray does not exist yet.
        events = []
        agent, clock = self.build(events, (40, 60, 90, 95), dry_run=True)
        points = ((106, 1640), ((280, 1265),))
        with patch('main.emote_points', return_value=points), \
                patch.object(config, 'EMOTE_FIRST_DELAY_SECONDS', 0.0), \
                patch('main.time.perf_counter', side_effect=lambda: clock[0]), patch('main.time.sleep'):
            agent.run(once=True)
        emotes = [d for e, d in events if e == 'dry_run_emote']
        self.assertEqual([d['tick'] for d in emotes], [90])
        agent.executor.close()

    def test_an_unverified_tray_disables_emotes_without_stopping_the_battle(self):
        # Points the loader at its own unverified file so the result does not
        # depend on whether this machine has already calibrated the tray.
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        unverified = Path(directory.name) / 'emote.json'
        unverified.write_text(json.dumps({**CALIBRATION, 'verified': False}), encoding='utf-8')
        events = []
        agent, clock = self.build(events, (90, 95), dry_run=True)
        with patch.object(config, 'EMOTE_CALIBRATION_PATH', unverified), \
                patch.object(config, 'EMOTE_FIRST_DELAY_SECONDS', 0.0), \
                patch('main.time.perf_counter', side_effect=lambda: clock[0]), patch('main.time.sleep'):
            agent.run(once=True)
        self.assertIn('emote_input_disabled', [e for e, _ in events])
        self.assertEqual([d['tick'] for e, d in events if e == 'decision'], [90, 95])
        self.assertNotIn('dry_run_emote', [e for e, _ in events])
        agent.actuator.emote.assert_not_called()
        agent.executor.close()

    def test_a_failed_tap_disables_emotes_and_is_never_replayed(self):
        events = []
        agent, clock = self.build(events, (90, 95, 100, 105), dry_run=False,
                                  emote_side_effect=RuntimeError('ADB command timeout'))
        points = ((106, 1640), ((280, 1265),))
        with patch('main.emote_points', return_value=points), \
                patch.object(config, 'EMOTE_FIRST_DELAY_SECONDS', 0.0), \
                patch('main.time.perf_counter', side_effect=lambda: clock[0]), patch('main.time.sleep'):
            agent.run(once=True)
        self.assertEqual(agent.actuator.emote.call_count, 1)
        disabled = [d for e, d in events if e == 'emote_disabled']
        self.assertEqual(len(disabled), 1)
        self.assertIn('ADB command timeout', disabled[0]['reason'])
        self.assertNotIn('emote_sent', [e for e, _ in events])
        self.assertEqual([d['tick'] for e, d in events if e == 'decision'], [90, 95, 100, 105])
        agent.executor.close()


    def test_a_refused_tap_disables_emotes_without_killing_the_match(self):
        # The ability-HUD guard raises ValueError; that must cost a demote, not
        # the battle.
        events = []
        agent, clock = self.build(events, (90, 95, 100, 105), dry_run=False,
                                  emote_side_effect=ValueError('emote slot too close to the hero ability HUD'))
        points = ((106, 1640), ((280, 1265),))
        with patch('main.emote_points', return_value=points), \
                patch.object(config, 'EMOTE_FIRST_DELAY_SECONDS', 0.0), \
                patch('main.time.perf_counter', side_effect=lambda: clock[0]), patch('main.time.sleep'):
            agent.run(once=True)
        self.assertEqual(agent.actuator.emote.call_count, 1)
        self.assertEqual(len([d for e, d in events if e == 'emote_disabled']), 1)
        self.assertNotIn('fatal_error', [e for e, _ in events])
        self.assertEqual([d['tick'] for e, d in events if e == 'decision'], [90, 95, 100, 105])
        agent.executor.close()


if __name__ == '__main__':
    unittest.main()
