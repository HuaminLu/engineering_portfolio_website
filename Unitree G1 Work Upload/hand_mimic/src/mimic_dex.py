#!/usr/bin/env python3
"""Pure dex-retargeting mimic — DexPilot solver, right hand → right Inspire.

No geometric fallback, no hybrid. Just dex-retargeting (DexPilot) end-to-end.
Optimised for pinch quality.

    uv run python src/mimic_dex.py --source 9 --mirror --gpu
    uv run python src/mimic_dex.py --right-port none   # no hardware, overlay only
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from _source import FrameSource
from dexkit import get_logger
from dexkit.mapping import AngleSmoother
from dexkit.mapping.dex_backend import DexRetarget
from dexkit.perception import HandTracker, draw_landmarks

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
LOG_DIR       = PROJECT_ROOT / "logs"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"


def _physical_side(label: str, swap: bool) -> str:
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
    ap = argparse.ArgumentParser(description="Pure dex-retargeting mimic")
    ap.add_argument("--source",      default="9")
    ap.add_argument("--model",       type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--width",       type=int,  default=1920)
    ap.add_argument("--height",      type=int,  default=1080)
    ap.add_argument("--left-port",   default="/dev/ttyUSB0")
    ap.add_argument("--right-port",  default="/dev/ttyUSB1")
    ap.add_argument("--baud",        type=int,  default=115200)
    ap.add_argument("--hand-speed",  type=int,  default=1000)
    ap.add_argument("--fps",         type=float, default=60.0)
    ap.add_argument("--alpha",       type=float, default=0.8)
    ap.add_argument("--deadband",    type=int,  default=5)
    # swap=True: MediaPipe "Left" (mirrored right hand) → right Inspire
    ap.add_argument("--no-swap",     dest="swap", action="store_false")
    ap.set_defaults(swap=False)
    ap.add_argument("--gpu",         action="store_true")
    ap.add_argument("--mirror",      action="store_true")
    ap.add_argument("--windowed",    dest="fullscreen", action="store_false")
    ap.set_defaults(fullscreen=True)
    ap.add_argument("--dry-run",     action="store_true")
    args = ap.parse_args()

    log = get_logger("mimic_dex", log_dir=LOG_DIR)
    log.info("pure dex-retargeting mode (DexPilot)")

    try:
        source = FrameSource(args.source, width=args.width, height=args.height)
    except Exception:
        log.exception("cannot open source %s", args.source); return 1
    if not source.opened():
        log.error("source not opened"); return 1
    log.info("source opened %s", source.describe())

    try:
        tracker = HandTracker(str(args.model), num_hands=2,
                              delegate="gpu" if args.gpu else "cpu")
    except Exception:
        log.exception("tracker failed"); source.release(); return 1

    # DexPilot retargeters — one per chirality (MediaPipe label, not physical side)
    try:
        dex = {
            "left":  DexRetarget("left",  low_pass_alpha=1.0, use_dexpilot=True),
            "right": DexRetarget("right", low_pass_alpha=1.0, use_dexpilot=True),
        }
        log.info("dex-retargeting loaded (DexPilot)")
    except Exception:
        log.exception("failed to load dex-retargeting"); source.release(); tracker.close(); return 1

    def make_smoother():
        return AngleSmoother(alpha=[args.alpha] * 6)

    channels: dict[str, dict] = {}
    if not args.dry_run:
        from dexkit.hands import InspireHand
        for side, port in {"left": args.left_port, "right": args.right_port}.items():
            if port.lower() == "none":
                continue
            try:
                h = InspireHand(port=port, baud=args.baud, hand_id=1)
                h.set_speed([args.hand_speed] * 6)
                h.set_force([400] * 6)
                channels[side] = {"hand": h, "smoother": make_smoother(),
                                   "last_sent": None, "last_cmd": 0.0}
                log.info("connected %s %s", side, port)
            except Exception:
                log.exception("failed %s %s", side, port)
    if not channels:
        for side in ("left", "right"):
            channels[side] = {"hand": None, "smoother": make_smoother(),
                               "last_sent": None, "last_cmd": 0.0}

    win = "mimic_dex"
    min_dt = 1.0 / args.fps if args.fps > 0 else 0.0

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    if args.fullscreen:
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    print("q=quit  f=fullscreen")

    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed"); break
            if args.mirror:
                frame = cv2.flip(frame, 1)

            result = tracker.process(frame)
            draw_landmarks(frame, result)

            cv2.putText(frame, "DEX (v1)", (10, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)

            seen: set[str] = set()
            now = time.monotonic()

            for i in range(result.num_hands):
                label = result.handedness[i] if i < len(result.handedness) else "?"
                # physical routing (swap=True corrects mirror flip)
                side = _physical_side(label, args.swap)
                ch = channels.get(side)
                if ch is None:
                    continue
                seen.add(side)

                # dex uses the MediaPipe chirality label for frame alignment
                dr = dex.get(label.lower())
                if dr is None:
                    continue

                wl = (result.world_landmarks[i]
                      if result.world_landmarks and i < len(result.world_landmarks)
                      else result.landmarks[i])
                try:
                    da = dr.retarget(wl)
                except Exception:
                    log.exception("dex retarget failed (%s)", side); continue

                angles = ch["smoother"].update(da)
                cv2.putText(frame, f"{side}: {angles}",
                            (10, 30 + 26 * len(seen)),
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

    except Exception:
        log.exception("loop crashed"); return 1
    finally:
        for ch in channels.values():
            if ch["hand"]: ch["hand"].close()
        tracker.close(); source.release(); cv2.destroyAllWindows()
        log.info("mimic_dex closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
