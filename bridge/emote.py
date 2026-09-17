"""Calibrated in-battle emote tray: coordinates, guard and pacing.

The tray is a fixed two-tap sequence (open button, then one slot).  Unlike card
deployment it carries no game outcome, so it is deliberately kept out of the
policy: the scheduler only fills idle decision windows and never competes with
an action for the single ADB input lane.
"""
import json
import math
import random
from pathlib import Path


class EmoteError(ValueError):
    """The emote tray geometry is missing, unverified or malformed."""


def _is_finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def emote_points(path, size):
    """Load the verified tray geometry and scale it to the touch ``size``.

    Returns ``(button, slots)`` in device pixels.  The calibration records the
    screen it was measured on, so a resized emulator is rejected rather than
    silently mistranslated.
    """
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('verified') is not True:
        raise EmoteError('emote tray is not calibrated')
    try:
        width, height = data['size']
        button = data['emote_button']
        raw_slots = data['emote_slots']
    except (KeyError, TypeError, ValueError) as exc:
        raise EmoteError('invalid emote calibration') from exc
    if not _is_finite_number(width) or not _is_finite_number(height) or width <= 0 or height <= 0:
        raise EmoteError('invalid emote calibration size')
    if not isinstance(raw_slots, (list, tuple)) or not raw_slots:
        raise EmoteError('emote calibration has no slots')
    if not isinstance(button, (list, tuple)) or len(button) != 2 or not all(map(_is_finite_number, button)):
        raise EmoteError('invalid emote button')
    if not (0 <= button[0] < width and 0 <= button[1] < height):
        raise EmoteError('emote button outside screen')
    slots = []
    for slot in raw_slots:
        if not isinstance(slot, (list, tuple)) or len(slot) != 2 or not all(map(_is_finite_number, slot)):
            raise EmoteError('invalid emote slot')
        if not (0 <= slot[0] < width and 0 <= slot[1] < height):
            raise EmoteError('emote slot outside screen')
        slots.append((slot[0], slot[1]))
    if abs(size[0] / size[1] - width / height) > .005:
        raise EmoteError('emote calibration screen aspect ratio changed')
    scale_x, scale_y = size[0] / width, size[1] / height
    return ((round(button[0] * scale_x), round(button[1] * scale_y)),
            tuple((round(x * scale_x), round(y * scale_y)) for x, y in slots))


class EmoteScheduler:
    """Pick a random slot on a randomized interval, honouring an opening delay."""

    def __init__(self, button, slots, *, first_delay, min_interval, max_interval, now):
        if not slots:
            raise EmoteError('emote scheduler needs at least one slot')
        if min_interval <= 0 or max_interval < min_interval or first_delay < 0:
            raise EmoteError('invalid emote interval')
        self.button, self.slots = tuple(button), tuple(slots)
        self.min_interval, self.max_interval = float(min_interval), float(max_interval)
        self.next_at = now + float(first_delay)

    def due(self, now):
        return now >= self.next_at

    def pick(self, rng=random):
        return rng.randrange(len(self.slots))

    def reschedule(self, now, rng=random):
        self.next_at = now + rng.uniform(self.min_interval, self.max_interval)
        return self.next_at


def emote_allowed(scheduler, executor, now):
    """True only in a genuine idle window: nothing queued, sent or in flight.

    ``future`` is the single worker that owns the ADB lane and ``pending``
    covers both the queued and the sent-but-unacknowledged states, so requiring
    all three to be clear is what keeps an emote from racing a card deploy.
    """
    return (scheduler is not None and scheduler.due(now) and executor.future is None
            and not executor.pending and not executor.fault)
