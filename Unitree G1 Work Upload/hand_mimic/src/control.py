#!/usr/bin/env python3
"""Hand control CLI — drive the Inspire RH56 over RS-485.

    python src/control.py open                       # named gesture
    python src/control.py --angles 0,0,0,1000,0,0    # explicit 6-DOF
    python src/control.py open --dry-run             # print, don't open serial
    python src/control.py close --port /dev/ttyUSB0 --hand-id 1

Joint order: [pinky, ring, middle, index, thumb_bend, thumb_rot]; 0=closed, 1000=open.
Connections and errors are logged to logs/control.log.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from dexkit import get_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"

# App-level preset poses (order: pinky, ring, middle, index, thumb_bend, thumb_rot).
GESTURES: dict[str, list[int]] = {
    "open": [1000, 1000, 1000, 1000, 1000, 1000],
    "close": [0, 0, 0, 0, 0, 500],
    "pinch": [1000, 1000, 1000, 0, 0, 500],
    "point": [0, 0, 0, 1000, 0, 0],
    "thumbs_up": [0, 0, 0, 0, 1000, 0],
    "peace": [0, 0, 1000, 1000, 0, 0],
}


def parse_angles(text: str) -> list[int]:
    parts = [int(x) for x in text.split(",")]
    if len(parts) != 6:
        raise argparse.ArgumentTypeError("--angles needs 6 comma-separated values")
    return parts


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspire RH56 control CLI")
    ap.add_argument("gesture", nargs="?", choices=sorted(GESTURES),
                    help="named preset pose")
    ap.add_argument("--angles", type=parse_angles,
                    help="explicit 6 angles 'p,r,m,i,tb,tr' (0-1000)")
    ap.add_argument("--port", default="/dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--hand-id", type=int, default=1)
    ap.add_argument("--speed", type=int, default=500, help="0-1000 per DOF")
    ap.add_argument("--force", type=int, default=500, help="0-1000 grams per DOF")
    ap.add_argument("--dry-run", action="store_true",
                    help="print target angles without opening the serial port")
    args = ap.parse_args()

    log = get_logger("control", log_dir=LOG_DIR)

    if args.angles is not None:
        angles = args.angles
    elif args.gesture is not None:
        angles = GESTURES[args.gesture]
    else:
        ap.error("provide a gesture or --angles")

    if args.dry_run:
        log.info("[dry-run] target angles=%s (port=%s id=%d)",
                 angles, args.port, args.hand_id)
        print(f"[dry-run] would send angles: {angles}")
        return 0

    # Import here so --dry-run works without the serial dependency present.
    from dexkit.hands import InspireHand

    try:
        hand = InspireHand(port=args.port, baud=args.baud, hand_id=args.hand_id)
        log.info("connected port=%s baud=%d id=%d", args.port, args.baud, args.hand_id)
    except Exception:
        log.exception("failed to open hand on %s", args.port)
        return 1

    try:
        hand.set_speed([args.speed] * 6)
        hand.set_force([args.force] * 6)
        if not hand.set_angles(angles):
            log.error("set_angles returned failure for %s", angles)
            return 1
        log.info("set angles=%s", angles)
        print(f"sent angles: {angles}")
    except Exception:
        log.exception("control command failed")
        return 1
    finally:
        hand.close()
        log.info("hand closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
