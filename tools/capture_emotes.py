"""Capture one screenshot per ``emote_sent`` so each tray slot can be eyeballed.

This is the tool that produces the evidence for ``emote_calibration.local.json``.
Run a battle with ``--emote`` and point this at the AI's JSONL: for every
``emote_sent`` event it grabs a frame immediately.  The in-game emote bubble
stays visible for a couple of seconds, so a capture that starts within ~1 s of
the send lands on it.

Frames are downscaled to keep a whole match well under 100 MB.  The summary at
the end lists which slot indices were seen, so a slot that never appeared is
obvious before you write the calibration file.

The ADB path and serial come from the active settings file, so this needs no
host-specific constants of its own::

    .venv\\Scripts\\python.exe tools\\capture_emotes.py \\
        --log runs\\match1.jsonl --output diagnostics\\emotes

Press Ctrl+C to stop early; the summary still prints.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config

DEFAULT_IDLE = 90.0
DEFAULT_THUMBNAIL = (540, 960)


def parse_size(text: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in text.lower().split('x', 1))
    except ValueError as error:
        raise argparse.ArgumentTypeError('expected WIDTHxHEIGHT, e.g. 540x960') from error
    if width < 1 or height < 1:
        raise argparse.ArgumentTypeError('thumbnail dimensions must be positive')
    return width, height


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--log', type=Path, required=True,
                        help='JSONL written by the running AI (main.py --log or multi_match)')
    parser.add_argument('--output', type=Path, required=True,
                        help='Directory to write the per-slot frames into')
    parser.add_argument('--idle', type=float, default=DEFAULT_IDLE,
                        help=f'Seconds to wait for the next emote before stopping (default: {DEFAULT_IDLE:g})')
    parser.add_argument('--thumbnail', type=parse_size, default=DEFAULT_THUMBNAIL,
                        help='Saved frame size as WIDTHxHEIGHT (default: 540x960)')
    parser.add_argument('--adb', default=None, help='ADB path; defaults to the settings file')
    parser.add_argument('--serial', default=None, help='ADB serial; defaults to the settings file')
    return parser


def capture(adb: str, serial: str, destination: Path) -> None:
    completed = subprocess.run([adb, '-s', serial, 'exec-out', 'screencap', '-p'],
                               stdout=subprocess.PIPE, check=True, timeout=30)
    destination.write_bytes(completed.stdout)


def main() -> int:
    options = build_parser().parse_args()
    adb = options.adb or str(config.ADB_PATH)
    serial = options.serial or config.ADB_SERIAL
    if not serial:
        print('No ADB serial configured; set adb_serial in the settings file or pass --serial.',
              flush=True)
        return 2
    if options.idle <= 0:
        print('--idle must be positive.', flush=True)
        return 2

    log = options.log.resolve()
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    scratch = output / '_frame.png'

    print(f'log    : {log}')
    print(f'frames : {output}')
    print(f'watching for emote_sent; stops after {options.idle:g}s of quiet. Ctrl+C to stop now.',
          flush=True)

    seen: set = set()
    captured: list[tuple[object, object, float]] = []
    deadline = time.time() + options.idle

    try:
        while time.time() < deadline:
            if log.is_file():
                try:
                    lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
                except OSError:
                    lines = []
                for line in lines:
                    if '"emote_sent"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    tick = record.get('tick')
                    if tick in seen:
                        continue
                    seen.add(tick)
                    started = time.time()
                    try:
                        capture(adb, serial, scratch)
                        name = f'slot{record.get("index")}_tick{tick}.png'
                        with Image.open(scratch) as image:
                            image.convert('RGB').resize(options.thumbnail, Image.LANCZOS).save(output / name)
                        captured.append((record.get('index'), tick, time.time() - started))
                        print(f'captured {name} in {time.time() - started:.2f}s', flush=True)
                    except Exception as error:  # the monitor must outlive one bad frame
                        print(f'capture failed at tick {tick}: {error!r}', flush=True)
                    deadline = time.time() + options.idle
            time.sleep(.25)
    except KeyboardInterrupt:
        print('\ninterrupted', flush=True)
    finally:
        if scratch.exists():
            scratch.unlink()

    print(f'total frames: {len(captured)}')
    for index, tick, took in captured:
        print(f'  slot index {index}  tick {tick}  capture {took:.2f}s')
    if captured:
        print(f'slot indices seen: {sorted({index for index, _, _ in captured})}')
        print('Compare every frame against your tray; a slot you never saw is not calibrated.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
