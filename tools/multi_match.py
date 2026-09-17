"""Run bounded consecutive matches through the real lobby/result flow.

The supervisor drives the matchmaking lifecycle that main.py intentionally
leaves to the operator. One state machine instance runs per session::

    PREPARE -> LAUNCH -> BIND -> VERIFY -> RESULT_OK -> (next match)

Each match launches one supervised `main.py --once --start-battle` child:
the child taps the battle button, plays exactly one battle, and exits at the
native terminal. During the battle the child is the only touch source; the
supervisor taps only between matches.

Lifecycle signal caveat (measured on this project's probe): after a battle
the game keeps stepping a state manager outside battles, so the probe serves
the frozen terminal snapshot indefinitely -- a real lobby is therefore
indistinguishable from a still-open result screen. The supervisor never
waits for an idle probe. It instead:

* treats `validated && finalized` as `maybe a result screen` and taps each
  known OK position once per dismissal round (never the screen center).
  Milestone-crossing wins queue several identical trophy road pages, so the
  result phase repeats the round a bounded number of times,
* binds a match only on a fresh live snapshot (`in_battle` without a
  finalized result): terminal snapshots keep their frozen tick, so only a
  genuinely new battle produces one,
* fails the whole session when the battle tap never produces a live battle,
  when the child exits without terminal evidence, or when a stage times out.
  A failed session never retries on its own.

`--dry-run` prints and logs the plan without touching ADB, the probe, or any
process.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

BASE_DIR = Path(__file__).resolve().parents[1]

# 1080x1920 reference layout. Three known OK positions: the standard battle
# result OK, the trophy road progression OK, and the OK of the ladder
# two-button layout (再来一场 + 确定). Each tap is harmless when its screen is
# not showing (empty area, or re-selecting the already active battle tab).
RESULT_OK_POSITIONS = (config.LOBBY_RESULT_DISMISS, (544, 1841), (707, 1738))
# Ladder result screens offer a left-hand "再来一场" (play again) button that
# queues the next match directly, skipping the lobby entirely. Measured from
# the operator's native capture; the buttons only become interactive a few
# seconds after the native terminal, so the caller must allow settle time.
REMATCH_BUTTON = (372, 1761)


def classify_probe(data: dict[str, Any] | None) -> str:
    """Map one raw probe response to lobby / live / finalized / unreachable.

    Only `battle_result.validated && finalized` together count as a terminal
    snapshot; anything less must be treated as a live battle (fail-closed).
    """

    if data is None:
        return 'unreachable'
    if not data.get('in_battle'):
        return 'lobby'
    result = data.get('battle_result')
    if isinstance(result, dict) and result.get('validated') is True and result.get('finalized') is True:
        return 'finalized'
    return 'live'


class JsonlLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, event: dict[str, Any]) -> None:
        row = {'time': time.time(), **event}
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')


@dataclass(frozen=True)
class SupervisorOptions:
    checkpoint: str
    matches: int
    battle_timeout: float
    matchmaking_timeout: float
    live_battle_wait: float
    result_settle: float
    lobby_settle: float
    poll_interval: float
    result_rounds: int
    launch_attempts: int
    rematch: bool
    unreachable_grace: float
    recover_attempts: int
    dry_run: bool
    output: Path


@dataclass(frozen=True)
class SpawnResult:
    returncode: int | None
    timed_out: bool


class ChildProcess:
    """Minimal handle the supervisor needs from a launched main.py child."""

    def __init__(self, process: subprocess.Popen) -> None:
        self._process = process

    def wait(self, timeout: float) -> int:
        return self._process.wait(timeout=timeout)

    def poll(self) -> int | None:
        return self._process.poll()

    def kill(self) -> None:
        self._process.kill()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


class MatchSupervisor:
    def __init__(self, options: SupervisorOptions, logger: JsonlLog, *, probe=None, tapper=None,
                 spawn: Callable[[Path], ChildProcess] | None = None,
                 restart: Callable[[], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.options = options
        self.probe = probe
        self.tapper = tapper
        self.spawn = spawn
        self.restart = restart
        self.logger = logger
        self._sleep = sleep
        self._clock = clock
        self._match_number = 0
        self.last_rematch_armed = False

    def record(self, event: str, **fields: Any) -> None:
        self.logger.record({'event': event, 'match': self._match_number, **fields})

    def enter(self, state: str) -> None:
        self.record('state_enter', state=state)
        print(f'[{self._match_number}] {state}', flush=True)

    def ai_log_path(self, number: int) -> Path:
        return self.options.output.parent / f'{self.options.output.stem}_match{number}.jsonl'

    def probe_snapshot(self) -> tuple[str, int | None]:
        """Return (state, tick) for one probe reading; tick is None when absent."""

        try:
            data = self.probe.query()
        except Exception:
            return 'unreachable', None
        if data is None:
            return 'unreachable', None
        tick = data.get('tick')
        return classify_probe(data), (tick if type(tick) is int else None)

    def probe_state(self) -> str:
        return self.probe_snapshot()[0]

    def prepare(self, label='prepare'):
        """Wait until the game is clear to launch, dismissing stale panels.

        A live battle before launch is one nobody is driving (e.g. a tap
        whose matchmaking registered after its child was killed). It usually
        ends on its own, so the supervisor waits it out for a bounded time;
        if it never finalizes (a driverless stalemate) the game is restarted
        once to clear it. After any battle the probe keeps serving the frozen
        terminal snapshot even at the lobby, so once the panel is dismissed
        a finalized reading is itself the launch-ready state.
        """

        for attempt in (1, 2):
            deadline = self._clock() + self.options.live_battle_wait
            dismissed = False
            last_state = None
            while self._clock() < deadline:
                last_state = self.probe_state()
                if last_state != 'live':
                    if last_state == 'finalized' and not dismissed:
                        for coordinate in RESULT_OK_POSITIONS:
                            self.record('result_tap', coordinate=list(coordinate),
                                        basis='prepare_cleanup', label=label)
                            self.tapper(*coordinate)
                            self._sleep(self.options.lobby_settle)
                        dismissed = True
                    # A dismissed finalized reading is the frozen lobby itself
                    # (the probe keeps serving the last terminal snapshot), so
                    # it is launch-ready; a first-read finalized may still be
                    # a real result screen, so give the taps a moment and
                    # re-read once before deciding.
                    if last_state == 'lobby' or (last_state == 'finalized' and dismissed):
                        self.record('prepare_ready', state=last_state, label=label)
                        return True
                    self._sleep(self.options.poll_interval)
                    continue
                self._sleep(self.options.poll_interval)
            if attempt == 2 or self.restart is None:
                break
            # A battle that never finalizes with no driver is a stalemate;
            # restarting the game forfeits it and returns to the lobby.
            self.record('stale_battle_restart', label=label, attempt=attempt)
            self.restart()
        self.record('match_failed', reason='prepare_timeout', label=label, last_state=last_state)
        return False

    def wait_for_live(self, deadline: float) -> bool:
        """Confirm a real new battle: live state whose tick advances.

        The child is already listening, so this runs as verification after
        launch rather than as a precondition. A phantom reading -- the game
        briefly stepping a battle manager while the previous result screen is
        still up -- reports in_battle with a frozen tick, so it can never
        satisfy the advancing-tick requirement. A real battle ticks every
        50 ms and satisfies it within one poll interval.
        """

        previous_tick = None
        while self._clock() < deadline:
            state, tick = self.probe_snapshot()
            if state == 'live':
                if previous_tick is not None and tick is not None and tick > previous_tick:
                    self.record('match_bound', basis='probe_tick_advancing', tick=tick)
                    return True
                previous_tick = tick
            else:
                previous_tick = None
            self._sleep(self.options.poll_interval)
        return False

    def wait_child(self, child: ChildProcess, deadline: float) -> str:
        """Wait for the match child while watching the probe for a dead game.

        Returns 'finished' when the child exits, 'game_lost' when the probe
        stays unreachable past the grace window (the emulator or game process
        died mid-battle), and 'timeout' when neither happens in budget. The
        child cannot outlive its game, so a lost game is reported promptly
        instead of burning the whole battle budget on it.
        """

        unreachable_since = None
        while self._clock() < deadline:
            if child.poll() is not None:
                return 'finished'
            state = self.probe_state()
            if state == 'unreachable':
                if unreachable_since is None:
                    unreachable_since = self._clock()
                elif self._clock() - unreachable_since >= self.options.unreachable_grace:
                    self.record('game_unreachable', seconds=self._clock() - unreachable_since)
                    return 'game_lost'
            else:
                unreachable_since = None
            self._sleep(self.options.poll_interval)
        return 'timeout'

    def dismiss_overlays(self) -> None:
        """One round: a single tap at each known OK position; never the center.

        Milestone-crossing wins queue several identical trophy road pages, so
        the caller repeats this round a bounded number of times -- one tap per
        page, exactly what a human does, and never a center-screen tap that
        pages through rewards.
        """

        for coordinate in RESULT_OK_POSITIONS:
            self.record('result_tap', coordinate=list(coordinate), basis='one_per_screen')
            self.tapper(*coordinate)
            self._sleep(self.options.lobby_settle)

    def tap_rematch(self) -> None:
        self.record('rematch_tap', coordinate=list(REMATCH_BUTTON))
        self.tapper(*REMATCH_BUTTON)
        self._sleep(self.options.lobby_settle)

    def verify_terminal(self, ai_log: Path) -> dict[str, Any] | None:
        terminal = None
        try:
            with ai_log.open(encoding='utf-8') as stream:
                for line in stream:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(event, dict) and event.get('event') == 'battle_terminal':
                        terminal = event
        except OSError:
            return None
        return terminal

    def run_one(self, number: int) -> str:
        """Run one match. Returns 'ok', 'fail', or 'recover' (game died)."""
        self._match_number = number
        self.enter('PREPARE')
        if self.options.dry_run:
            for _ in range(self.options.result_rounds):
                for coordinate in RESULT_OK_POSITIONS:
                    self.record('result_tap', coordinate=list(coordinate), basis='dry_run')
            if self.options.rematch and number > 1:
                self.record('rematch_tap', coordinate=list(REMATCH_BUTTON), basis='dry_run')
            self.record('battle_tap', via='child --start-battle', coordinate=list(config.LOBBY_BATTLE_BTN))
            self.record('match_bound', basis='dry_run')
            self.record('match_end_detected', basis='dry_run')
            for _ in range(self.options.result_rounds):
                for coordinate in RESULT_OK_POSITIONS:
                    self.record('result_tap', coordinate=list(coordinate), basis='dry_run')
            return 'ok'

        # Match 1 (or a rematch that failed last time) needs the lobby flow;
        # rematch runs skip the lobby entirely and tap 再来一场 on the result
        # screen instead.
        if not (self.options.rematch and number > 1 and self.last_rematch_armed):
            if not self.prepare():
                return 'fail'
        use_start_battle = not (self.options.rematch and number > 1)
        ai_log = self.ai_log_path(number)
        for attempt in range(1, self.options.launch_attempts + 1):
            self.enter('LAUNCH')
            if attempt > 1:
                self.record('launch_retry', attempt=attempt)
                if self.options.rematch and attempt == 2:
                    # The first rematch tap landed during the result-screen
                    # animation; the button is interactive by now, so tap it
                    # once more before abandoning the direct path.
                    self.tap_rematch()
                else:
                    # Clear queued pages and fall back to the lobby battle
                    # button (the rematch queue did not register).
                    self.dismiss_overlays()
                    use_start_battle = True
            child = self.spawn(ai_log, start_battle=use_start_battle)
            try:
                deadline = self._clock() + self.options.battle_timeout
                if not self.wait_for_live(min(deadline, self._clock() + self.options.matchmaking_timeout)):
                    child.kill()
                    self.record('bind_attempt_failed', attempt=attempt)
                    continue
                self.enter('VERIFY')
                outcome = self.wait_child(child, deadline)
                if outcome == 'game_lost':
                    child.kill()
                    self.last_rematch_armed = False
                    return 'recover'
                if outcome == 'timeout':
                    child.kill()
                    self.record('match_failed', reason='battle_timeout')
                    self.last_rematch_armed = False
                    return 'fail'
            except BaseException:
                child.kill()
                raise
            terminal = self.verify_terminal(ai_log)
            if terminal is None:
                self.record('match_failed', reason='missing_battle_terminal', child_returncode=child.wait(0))
                self.last_rematch_armed = False
                return 'fail'
            self.record('match_finished', result=terminal.get('result'), crowns=terminal.get('crowns'),
                        tick=terminal.get('tick'))
            self.enter('RESULT_OK')
            self._sleep(self.options.result_settle)
            if self.options.rematch:
                # Arm the next match through the ladder rematch button; the
                # queued matchmaking registers while we finish this match.
                self.tap_rematch()
                self.last_rematch_armed = True
            else:
                for _ in range(self.options.result_rounds):
                    self.dismiss_overlays()
            self.record('match_complete', result=terminal.get('result'))
            return 'ok'
        self.record('match_failed', reason='battle_tap_no_effect')
        self.last_rematch_armed = False
        return 'fail'

    def run(self) -> int:
        number = 1
        recoveries = 0
        while number <= self.options.matches:
            try:
                outcome = self.run_one(number)
            except Exception as error:
                self.record('match_failed', reason='supervisor_exception', error=repr(error))
                print(f'[{number}] session failed: {error!r}', flush=True)
                return 2
            if outcome == 'ok':
                number += 1
                continue
            if outcome == 'recover' and recoveries < self.options.recover_attempts and self.restart is not None:
                recoveries += 1
                self.record('session_recover', match=number, attempt=recoveries,
                            reason='game_lost_mid_battle')
                print(f'[{number}] game lost mid-battle; restarting it and retrying', flush=True)
                try:
                    self.restart()
                except Exception as error:
                    self.record('match_failed', reason='restart_failed', error=repr(error))
                    return 2
                continue
            return 2
        self.record('run_complete', matches=self.options.matches, recoveries=recoveries)
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', default='hog26', help='checkpoint alias passed to main.py')
    parser.add_argument('--matches', type=int, default=1, help='Number of consecutive matches (bounded, no infinite loop)')
    parser.add_argument('--battle-timeout', type=float, default=600.0,
                        help='Overall per-match budget including loading and overtime')
    parser.add_argument('--matchmaking-timeout', type=float, default=90.0,
                        help='Seconds to wait for the tapped battle to produce a live battle')
    parser.add_argument('--live-battle-wait', type=float, default=300.0,
                        help='Seconds to wait out a live battle nobody is driving before failing')
    parser.add_argument('--unreachable-grace', type=float, default=45.0,
                        help='Seconds the probe may stay unreachable mid-battle before the session restarts the game')
    parser.add_argument('--recover-attempts', type=int, default=5,
                        help='Mid-session game restarts allowed when the emulator or game dies; then the session stops')
    parser.add_argument('--result-rounds', type=int, default=3,
                        help='Result dismissal rounds; milestone wins queue several OK pages')
    parser.add_argument('--launch-attempts', type=int, default=3,
                        help='Launch attempts per match: rematch retry, then lobby fallback')
    parser.add_argument('--rematch', action='store_true',
                        help='Use the ladder 再来一场 button between matches instead of the lobby battle button')
    parser.add_argument('--result-settle', type=float, default=6.0,
                        help='Seconds for the result screen to appear and its buttons to become interactive')
    parser.add_argument('--lobby-settle', type=float, default=2.0, help='Seconds after each result tap')
    parser.add_argument('--poll-interval', type=float, default=0.5)
    parser.add_argument('--dry-run', action='store_true', help='Print and log the plan without ADB, probe, or matches')
    parser.add_argument('--output', type=Path, default=Path('logs/multi_match_log.jsonl'),
                        help='Supervisor JSONL; per-match AI logs are derived from it')
    return parser


def options_from_args(args: argparse.Namespace) -> SupervisorOptions:
    if args.matches < 1:
        raise ValueError('--matches must be positive')
    numeric = {'battle_timeout': args.battle_timeout, 'matchmaking_timeout': args.matchmaking_timeout,
               'live_battle_wait': args.live_battle_wait, 'unreachable_grace': args.unreachable_grace,
               'result_settle': args.result_settle, 'lobby_settle': args.lobby_settle,
               'poll_interval': args.poll_interval}
    if any(value <= 0 for value in numeric.values()):
        raise ValueError('timeouts, settle windows, and poll interval must be positive')
    if args.result_rounds < 1 or args.launch_attempts < 1 or args.recover_attempts < 0:
        raise ValueError('result rounds and launch attempts must be positive; recover attempts cannot be negative')
    return SupervisorOptions(checkpoint=args.checkpoint, matches=args.matches,
                             battle_timeout=args.battle_timeout, matchmaking_timeout=args.matchmaking_timeout,
                             live_battle_wait=args.live_battle_wait,
                             result_settle=args.result_settle, lobby_settle=args.lobby_settle,
                             poll_interval=args.poll_interval, result_rounds=args.result_rounds,
                             launch_attempts=args.launch_attempts, rematch=args.rematch,
                             unreachable_grace=args.unreachable_grace,
                             recover_attempts=args.recover_attempts,
                             dry_run=args.dry_run, output=args.output)


def real_spawn(options: SupervisorOptions) -> Callable[..., ChildProcess]:
    def spawn(ai_log: Path, start_battle: bool = True) -> ChildProcess:
        command = [sys.executable, str(BASE_DIR / 'main.py'), '--checkpoint', options.checkpoint,
                   '--once', '--log', str(ai_log)]
        if start_battle:
            command.append('--start-battle')
        process = subprocess.Popen(command, cwd=BASE_DIR,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return ChildProcess(process)
    return spawn


def real_restart() -> None:
    """Forfeit a stalemate orphan battle by restarting the game app."""
    base = [str(config.ADB_PATH), '-s', config.ADB_SERIAL]
    subprocess.run(base + ['shell', 'am', 'force-stop', config.PACKAGE_NAME],
                   check=True, timeout=30, capture_output=True)
    subprocess.run(base + ['shell', 'am', 'start', '-n',
                           config.PACKAGE_NAME + '/' + config.ACTIVITY_NAME],
                   check=True, timeout=30, capture_output=True)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        options = options_from_args(args)
    except ValueError as error:
        parser.error(str(error))

    logger = JsonlLog(options.output)
    logger.record({'event': 'run_start', 'matches': options.matches, 'checkpoint': options.checkpoint,
                   'serial': config.ADB_SERIAL, 'dryRun': options.dry_run})
    if options.dry_run:
        supervisor = MatchSupervisor(options, logger=logger)
        code = supervisor.run()
        print(json.dumps({'matches': options.matches, 'checkpoint': options.checkpoint,
                          'battleButton': list(config.LOBBY_BATTLE_BTN),
                          'resultOkPositions': [list(c) for c in RESULT_OK_POSITIONS],
                          'output': str(options.output)}, ensure_ascii=False), flush=True)
        return code

    from bridge.actuator import Actuator
    from bridge.probe_client import ProbeClient
    try:
        actuator = Actuator()
        actuator.prepare()
    except Exception as error:
        logger.record({'event': 'run_failed', 'reason': 'device_unavailable', 'error': repr(error)})
        print(f'Device preparation failed: {error!r}', flush=True)
        return 2
    print('Lobby prerequisite: stay on the battle tab; the child taps the battle button itself.', flush=True)
    supervisor = MatchSupervisor(options, logger=logger, probe=ProbeClient(port=config.PROBE_PORT),
                                 tapper=actuator.tap, spawn=real_spawn(options), restart=real_restart)
    try:
        return supervisor.run()
    except KeyboardInterrupt:
        logger.record({'event': 'run_interrupted'})
        print('Interrupted by user; no further matches will be started.', flush=True)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
