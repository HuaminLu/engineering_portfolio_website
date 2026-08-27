#!/usr/bin/env python3
"""Pure geometric retargeting — no calibration, factory defaults only.

Uses the default Calibration() values from dexkit: MCP-PIP-DIP joint angles
mapped to the Inspire 0-1000 scale with fixed bounds (170°=open, 50°=closed).
No config file, no pinch detection, no snapping. Just raw geometry → hands.

    uv run python src/mimic_raw.py --source 9 --mirror --gpu
    uv run python src/mimic_raw.py --right-port none   # left hand only
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

import yaml

from _source import FrameSource
from _config import load_calibration
from dexkit import get_logger
from dexkit.mapping import AngleSmoother, landmarks_to_angles
from dexkit.mapping.retarget import Calibration
from dexkit.perception import HandTracker, draw_landmarks
from calibrate_hand import run_calibration

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
LOG_DIR       = PROJECT_ROOT / "logs"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "hand_mimic.yaml"


def _side(label: str, swap: bool) -> str:
    s = label.lower()
    if s not in ("left", "right"):
        return s
    return ("right" if s == "left" else "left") if swap else s


def _deadband(angles, last, db):
    if last is None:
        return list(angles), True
    out, changed = list(last), False
    for i in range(6):
        if abs(angles[i] - last[i]) >= db:
            out[i] = angles[i]; changed = True
    return out, changed



def main() -> int:
    ap = argparse.ArgumentParser(description="Raw geometric mimic — no calibration")
    ap.add_argument("--source",      default="9")
    ap.add_argument("--model",       type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--width",       type=int,  default=1920)
    ap.add_argument("--height",      type=int,  default=1080)
    ap.add_argument("--left-port",   default="/dev/ttyUSB0")
    ap.add_argument("--right-port",  default="/dev/ttyUSB1")
    ap.add_argument("--baud",        type=int,  default=115200)
    ap.add_argument("--hand-speed",  type=int,  default=1000)
    ap.add_argument("--fps",         type=float, default=60.0)
    ap.add_argument("--alpha",       type=float, default=0.6)
    ap.add_argument("--thumb-alpha", type=float, default=0.25)
    ap.add_argument("--deadband",    type=int,  default=8)
    ap.add_argument("--no-swap",     dest="swap", action="store_false")
    ap.set_defaults(swap=False)
    ap.add_argument("--gpu",         action="store_true")
    ap.add_argument("--mirror",      action="store_true")
    ap.add_argument("--windowed",    dest="fullscreen", action="store_false")
    ap.set_defaults(fullscreen=True)
    ap.add_argument("--config",      type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run",     action="store_true")
    args = ap.parse_args()

    log = get_logger("mimic_raw", log_dir=LOG_DIR)
    log.info("raw geometric mode")

    try:
        source = FrameSource(args.source, width=args.width, height=args.height)
    except Exception:
        log.exception("cannot open source %s", args.source); return 1
    if not source.opened():
        log.error("source not opened"); return 1
    log.info("source opened %s", source.describe())

    calib = load_calibration(args.config, log)

    try:
        tracker = HandTracker(str(args.model), num_hands=2,
                              delegate="gpu" if args.gpu else "cpu")
    except Exception:
        log.exception("tracker failed"); source.release(); return 1

    def make_smoother():
        return AngleSmoother(alpha=[args.alpha]*4 + [args.thumb_alpha]*2)

    channels: dict[str, dict] = {}
    if not args.dry_run:
        from dexkit.hands import InspireHand
        for side, port in {"left": args.left_port, "right": args.right_port}.items():
            if port.lower() == "none":
                continue
            try:
                h = InspireHand(port=port, baud=args.baud, hand_id=1)
                h.set_speed([args.hand_speed]*6)
                h.set_force([400]*6)
                channels[side] = {"hand": h, "smoother": make_smoother(),
                                   "last_sent": None, "last_cmd": 0.0}
                log.info("connected %s %s", side, port)
            except Exception:
                log.exception("failed %s %s", side, port)
    if not channels:
        for side in ("left", "right"):
            channels[side] = {"hand": None, "smoother": make_smoother(),
                               "last_sent": None, "last_cmd": 0.0}

    win = "mimic_raw"
    min_dt = 1.0 / args.fps if args.fps > 0 else 0.0
    frame_i = 0
    flip_z = False   # toggle with 'z' key — palm-facing mode

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    if args.fullscreen:
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    print("q=quit  f=fullscreen  z=flip palm/backhand  c=calibrate  t=reload config")

    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed"); break
            frame_i += 1
            if args.mirror:
                frame = cv2.flip(frame, 1)

            result = tracker.process(frame)
            draw_landmarks(frame, result)

            mode_label = "PURE GEOMETRIC (v1) | PALM" if flip_z else "PURE GEOMETRIC (v1) | BACKHAND"
            cv2.putText(frame, mode_label, (10, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            seen: set[str] = set()
            now = time.monotonic()

            for i in range(result.num_hands):
                label = result.handedness[i] if i < len(result.handedness) else "?"
                side  = _side(label, args.swap)
                rot_side = _side(label, True)
                ch = channels.get(side)
                if ch is None:
                    continue
                seen.add(side)

                try:
                    lm = result.landmarks[i]
                    if flip_z:
                        import numpy as _np
                        lm = _np.array(lm, dtype=_np.float32)
                        lm[:, 2] = -lm[:, 2]
                    raw, _ = landmarks_to_angles(lm, calib, hand_side=rot_side)
                except Exception:
                    log.exception("retarget failed %s", side); continue

                angles = ch["smoother"].update(raw)
                cv2.putText(frame, f"{side}: {angles}",
                            (10, 30 + 26*len(seen)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                if now - ch["last_cmd"] >= min_dt:
                    out, changed = _deadband(angles, ch["last_sent"], args.deadband)
                    if changed:
                        ch["last_cmd"] = now; ch["last_sent"] = out
                        if ch["hand"] is not None:
                            ch["hand"].set_angles(out, wait=False)
                        else:
                            print(f"[dry-run] {side}: {out}", end="\r")

            for side, ch in channels.items():
                if side not in seen:
                    ch["smoother"].reset(); ch["last_sent"] = None

            cv2.imshow(win, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("f"):
                args.fullscreen = not args.fullscreen
                cv2.setWindowProperty(
                    win, cv2.WND_PROP_FULLSCREEN,
                    cv2.WINDOW_FULLSCREEN if args.fullscreen else cv2.WINDOW_NORMAL)
            if key == ord("z"):
                flip_z = not flip_z
                log.info("palm mode: %s", flip_z)
            if key == ord("c") or key == ord("C"):
                log.info("entering calibration")
                run_calibration(source, tracker, win,
                                mirror=args.mirror, swap_hands=args.swap, log=log,
                                calib=calib)
                calib = load_calibration(args.config, log)
                for ch_ in channels.values():
                    ch_["smoother"] = make_smoother()
                    ch_["last_sent"] = None
            if key == ord("t") or key == ord("T"):
                calib = load_calibration(args.config, log)
                for ch_ in channels.values():
                    ch_["smoother"] = make_smoother()
                    ch_["last_sent"] = None
                log.info("config reloaded")

    except Exception:
        log.exception("loop crashed"); return 1
    finally:
        for ch in channels.values():
            if ch["hand"]: ch["hand"].close()
        tracker.close(); source.release(); cv2.destroyAllWindows()
        log.info("mimic_raw closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
