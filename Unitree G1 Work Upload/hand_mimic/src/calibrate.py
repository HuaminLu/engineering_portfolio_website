#!/usr/bin/env python3
"""Pinch calibration tool for the Inspire RH56 hands.

Flow for each pinch target (index / middle / both):
  1. Press the key once  -> relevant fingers go limp (force=0, backdrivable)
  2. Physically move the Inspire hand thumb to touch the target finger
  3. Press the key again -> reads actual sensor angles, stores as preset

Keys:
  a  — index pinch calibration (thumb + index go limp, then record)
  s  — middle pinch calibration (thumb + middle go limp, then record)
  d  — both-fingers pinch calibration
  w  — write recorded values to config/hand_mimic.yaml (left hand mirrored to right)
  r  — restore normal force/speed to all fingers (exit limp mode)
  q  — quit

Logs to logs/calibrate.log. Run from the hand_mimic project directory.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import yaml

from _source import FrameSource
from dexkit import get_logger
from dexkit.perception import HandTracker, draw_landmarks

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR      = PROJECT_ROOT / "logs"
CONFIG_PATH  = PROJECT_ROOT / "config" / "hand_mimic.yaml"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"

# Joint order: [pinky, ring, middle, index, thumb_bend, thumb_rot]
_NORMAL_SPEED = 1000
_NORMAL_FORCE = 400

# Which DOFs go limp for each mode (indices into the 6-DOF array)
# [pinky=0, ring=1, middle=2, index=3, thumb_bend=4, thumb_rot=5]
_LIMP_DOFS = {
    "index":  [3, 4, 5],  # index + both thumb DOFs
    "middle": [2, 4, 5],  # middle + both thumb DOFs
    "both":   [2, 3, 4, 5],
}

# Screen colours
_CLR = {
    "white":  (255, 255, 255),
    "yellow": (0,   255, 255),
    "green":  (0,   255, 0),
    "red":    (0,   0,   255),
    "orange": (0,   165, 255),
    "gray":   (160, 160, 160),
}


def _put(frame, text: str, y: int, colour=(255,255,255), scale=0.7, thickness=2):
    cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness)


def _set_limp(hand, mode: str, limp: bool):
    """Release or restore the DOFs for the given pinch mode.

    release_dofs(): reads current actual angles, sets speed=0 and
    target=actual on those DOFs so the actuator exerts near-zero torque
    and can be pushed by hand.
    restore_dofs(): restores normal speed/force so the hand holds position.
    """
    dofs = _LIMP_DOFS[mode]
    if limp:
        hand.release_dofs(dofs)
    else:
        hand.restore_dofs(dofs, speed=_NORMAL_SPEED, force=_NORMAL_FORCE)


def _read_and_log(hand, mode: str, log) -> dict:
    """Read actual angles from hand sensors and return as pinch preset dict."""
    angles = hand.get_angles()
    if angles is None:
        log.error("get_angles() returned None during %s calibration", mode)
        return {}
    bend = angles[4]
    rot  = angles[5]
    log.info("recorded %s pinch: bend=%d rot=%d (full angles: %s)", mode, bend, rot, angles)
    if mode == "index":
        return {"pinch_index_bend": bend, "pinch_index_rot": rot}
    if mode == "middle":
        return {"pinch_middle_bend": bend, "pinch_middle_rot": rot}
    if mode == "both":
        return {"pinch_both_bend": bend, "pinch_both_rot": rot}
    return {}


def _draw_overlay(frame, state: dict):
    """Draw calibration status and key hints onto the frame."""
    h, w = frame.shape[:2]

    # Semi-transparent dark bar at the bottom for key hints
    bar_h = 140
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, h - bar_h), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    y = h - bar_h + 28
    lh = 26  # line height

    # State banner
    mode    = state.get("mode")
    waiting = state.get("waiting", False)
    if mode and waiting:
        banner = f">> LIMP: move thumb to {mode.upper()} pinch, then press key again <<"
        _put(frame, banner, y, _CLR["orange"], scale=0.65, thickness=2)
    elif mode:
        _put(frame, f"Recorded {mode.upper()} pinch  ✓", y, _CLR["green"], scale=0.65)
    else:
        _put(frame, "Calibration mode — follow the prompts below", y, _CLR["yellow"], scale=0.65)
    y += lh + 4

    # Recorded values
    rec = state.get("recorded", {})
    vals = []
    if "pinch_index_bend"  in rec: vals.append(f"idx_bend={rec['pinch_index_bend']}  idx_rot={rec['pinch_index_rot']}")
    if "pinch_middle_bend" in rec: vals.append(f"mid_bend={rec['pinch_middle_bend']} mid_rot={rec['pinch_middle_rot']}")
    if "pinch_both_bend"   in rec: vals.append(f"both_bend={rec['pinch_both_bend']}  both_rot={rec['pinch_both_rot']}")
    if vals:
        _put(frame, "  " + "   |   ".join(vals), y, _CLR["white"], scale=0.52)
    y += lh

    # Key hints
    hints = [
        ("[A] index pinch",  "pinch_index_bend" in rec),
        ("[S] middle pinch", "pinch_middle_bend" in rec),
        ("[D] both pinch",   "pinch_both_bend"  in rec),
        ("[W] write config", bool(rec)),
        ("[R] restore force", False),
        ("[Q] quit",         False),
    ]
    x = 20
    for label, done in hints:
        colour = _CLR["green"] if done else _CLR["gray"]
        (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.52, 1)
        cv2.putText(frame, label, (x, y + lh), cv2.FONT_HERSHEY_SIMPLEX, 0.52, colour, 1)
        x += tw + 30
        if x > w - 120:
            x = 20; y += lh


def _write_config(recorded: dict, log):
    """Merge recorded pinch presets into hand_mimic.yaml."""
    if CONFIG_PATH.exists():
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
    else:
        cfg = {}

    calib = cfg.setdefault("calibration", {})
    for k, v in recorded.items():
        calib[k] = v

    CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
    log.info("wrote calibration to %s: %s", CONFIG_PATH, recorded)
    return cfg


def main() -> int:
    ap = argparse.ArgumentParser(description="Pinch calibration for Inspire RH56")
    ap.add_argument("--source", default="8")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--left-port",  default="/dev/ttyUSB0")
    ap.add_argument("--right-port", default="/dev/ttyUSB1")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="run without opening serial ports (shows overlay only)")
    args = ap.parse_args()

    log = get_logger("calibrate", log_dir=LOG_DIR)

    # Camera
    try:
        source = FrameSource(args.source, width=args.width, height=args.height)
    except Exception:
        log.exception("cannot open camera %s", args.source)
        return 1
    log.info("camera opened %s", source.describe())

    # Tracker
    try:
        tracker = HandTracker(str(args.model), num_hands=2,
                              delegate="gpu" if args.gpu else "cpu")
    except Exception:
        log.exception("failed to load tracker")
        source.release(); return 1

    # Hands (left is the calibration hand; right gets mirrored on write)
    hands = {}
    if not args.dry_run:
        from dexkit.hands import InspireHand
        for side, port in (("left", args.left_port), ("right", args.right_port)):
            if port.lower() == "none":
                continue
            try:
                h = InspireHand(port=port, baud=115200, hand_id=1)
                h.restore_dofs(list(range(6)), speed=_NORMAL_SPEED, force=_NORMAL_FORCE)
                hands[side] = h
                log.info("connected %s hand on %s", side, port)
            except Exception:
                log.warning("could not open %s hand on %s — skipping", side, port)

    # UI state
    state: dict = {"mode": None, "waiting": False, "recorded": {}}
    _MODE_KEY = {"a": "index", "s": "middle", "d": "both"}

    WIN = "hand_mimic calibrate"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    print("Calibration keys: A=index pinch  S=middle pinch  D=both  W=write  R=restore  Q=quit")
    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed"); break

            result = tracker.process(frame)
            draw_landmarks(frame, result)
            _draw_overlay(frame, state)

            cv2.imshow(WIN, frame)
            key = cv2.waitKey(1) & 0xFF
            ch = chr(key).lower() if 32 <= key < 127 else None

            if ch == "q":
                break

            elif ch == "r":
                # Restore all DOFs on all hands
                for h in hands.values():
                    h.restore_dofs(list(range(6)), speed=_NORMAL_SPEED, force=_NORMAL_FORCE)
                state["mode"] = None
                state["waiting"] = False
                log.info("restored normal force/speed")

            elif ch in _MODE_KEY:
                mode = _MODE_KEY[ch]
                if not state["waiting"] or state["mode"] != mode:
                    # First press → go limp
                    state["mode"] = mode
                    state["waiting"] = True
                    for h in hands.values():
                        _set_limp(h, mode, limp=True)
                    log.info("%s pinch: went limp — physically move thumb to target", mode)
                else:
                    # Second press → record
                    recorded = {}
                    if hands:
                        # Read from left hand (or whichever is available)
                        h = hands.get("left") or next(iter(hands.values()))
                        recorded = _read_and_log(h, mode, log)
                    else:
                        # Dry-run: use placeholder zeros
                        log.warning("dry-run: no hand connected, recording zeros")
                        recorded = {f"pinch_{mode}_bend": 0, f"pinch_{mode}_rot": 500}

                    state["recorded"].update(recorded)
                    state["waiting"] = False

                    # Restore force so hand holds the recorded position
                    for h in hands.values():
                        _set_limp(h, mode, limp=False)
                    log.info("%s pinch recorded: %s", mode, recorded)

            elif ch == "w":
                if not state["recorded"]:
                    log.warning("nothing recorded yet — calibrate at least one pinch first")
                else:
                    _write_config(state["recorded"], log)
                    # Visual confirmation
                    conf = frame.copy()
                    cv2.rectangle(conf, (0, 0), (conf.shape[1], 80), (0, 80, 0), -1)
                    _put(conf, f"Saved to {CONFIG_PATH}", 50, _CLR["green"], scale=0.8)
                    cv2.imshow(WIN, conf)
                    cv2.waitKey(1500)

    except Exception:
        log.exception("calibration loop crashed")
        return 1
    finally:
        for h in hands.values():
            h.restore_dofs(list(range(6)), speed=_NORMAL_SPEED, force=_NORMAL_FORCE)
            h.close()
        tracker.close()
        source.release()
        cv2.destroyAllWindows()
        log.info("calibrate closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
