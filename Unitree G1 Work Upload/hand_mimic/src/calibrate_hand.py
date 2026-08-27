#!/usr/bin/env python3
"""Guided hand-range calibration — maps YOUR real hand extremes to the Inspire
mechanical limits. Writes per-finger curl bounds + thumb bend/rot bounds to
config/hand_mimic.yaml.

Menu-driven: pick which step(s) to (re)calibrate, then save only those.

    uv run python src/calibrate_hand.py --source 9 --mirror --gpu

Menu keys:
    1-5  run that calibration step
    A    run all steps in sequence
    S    save the steps calibrated THIS session (merges into config)
    Q    quit (from a step: back to menu; from menu: exit)

Steps:
  1. OPEN PALM (fingers straight)            -> 4-finger OPEN bound  (max angle)
  2. FIST (curl 4 fingers TIGHT, thumb aside)-> 4-finger CLOSED bound (min angle)
  3. FLAT PALM, THUMB OUT to the side        -> thumb rotation SPREAD
  4. THUMB UP off palm, aligned w/ MIDDLE    -> thumb rotation TUCKED + bend OPEN
  5. THUMB STRAIGHT, flat against index/palm -> thumb bend CLOSED
"""

from __future__ import annotations

import argparse
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
import yaml

from _source import FrameSource
from dexkit import get_logger
from dexkit.mapping import Calibration, landmarks_to_angles
from dexkit.mapping.retarget import (
    finger_curl_deg, thumb_curl_metric, thumb_rot_metric, _palm_size,
)
from dexkit.perception import HandTracker, draw_landmarks


def _pinch_dist(lm) -> float:
    lma = np.asarray(lm, dtype=np.float32)
    return float(np.linalg.norm(lma[4] - lma[8])) / _palm_size(lma)

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
LOG_DIR       = PROJECT_ROOT / "logs"
CONFIG_PATH   = PROJECT_ROOT / "config" / "hand_mimic.yaml"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"

FINGERS      = ["pinky", "ring", "middle", "index"]
HOLD_SECONDS = 3.0
AVG_FRAMES   = 8

# key, instruction, capture-kind, aggregation
STEPS = [
    ("fingers_open",   "1/6  OPEN PALM — all fingers straight & spread",             "fingers",    "max"),
    ("fingers_closed", "2/6  FIST — curl the 4 fingers TIGHT (thumb out of way)",    "fingers",    "min"),
    ("thumb_rot_max",  "3/6  FLAT PALM, THUMB OUT to the side (90 deg from index)",  "thumb_rot",  "avg"),
    ("thumb_mid",      "4/6  THUMB UP off the palm, aligned with MIDDLE finger",     "thumb_both", "avg"),
    ("thumb_curl_min", "5/6  THUMB STRAIGHT, laid flat against the index/palm",      "thumb_bend", "avg"),
    ("index_pinch",    "6/6  OK GESTURE — pinch thumb+index, hold (red dots show)",  "pinch",      "avg"),
]


def _capture(kind: str, lm, left: bool):
    if kind == "fingers":
        return {f: finger_curl_deg(lm, f) for f in FINGERS}
    if kind == "thumb_rot":
        return thumb_rot_metric(lm, left=left)
    if kind == "thumb_bend":
        return thumb_curl_metric(lm)
    if kind == "thumb_both":
        return {"rot": thumb_rot_metric(lm, left=left), "bend": thumb_curl_metric(lm)}
    return None


def _aggregate(buf: deque, kind: str, agg: str):
    fn = {"min": min, "max": max, "avg": lambda xs: float(np.mean(xs))}[agg]
    if kind == "fingers":
        return {f: float(fn([b[f] for b in buf])) for f in FINGERS}
    if kind == "thumb_both":
        return {"rot":  float(fn([b["rot"]  for b in buf])),
                "bend": float(fn([b["bend"] for b in buf]))}
    if kind == "pinch":
        ang = [float(np.mean([b["angles"][j] for b in buf])) for j in range(6)]
        return {"angles": ang, "dist": float(np.mean([b["dist"] for b in buf]))}
    return float(fn(list(buf)))


def _save_session(session: dict, log) -> list[str]:
    """Merge only the steps captured this session into config. Returns keys saved."""
    cfg   = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    calib = cfg.setdefault("calibration", {})
    saved = []

    if "fingers_open" in session or "fingers_closed" in session:
        pf = dict(calib.get("per_finger", {}))
        for f in FINGERS:
            entry = list(pf.get(f, [50.0, 170.0]))   # [closed, open] defaults
            if "fingers_closed" in session:
                entry[0] = round(session["fingers_closed"][f], 1)
            if "fingers_open" in session:
                entry[1] = round(session["fingers_open"][f], 1)
            pf[f] = entry
        calib["per_finger"] = pf
        if "fingers_open" in session:   saved.append("fingers_open")
        if "fingers_closed" in session: saved.append("fingers_closed")

    if "thumb_rot_max" in session:
        calib["thumb_rot_x_spread"] = round(session["thumb_rot_max"], 4); saved.append("thumb_rot_max")
    if "thumb_mid" in session:
        calib["thumb_rot_x_tucked"] = round(session["thumb_mid"]["rot"], 4)
        calib["thumb_curl_far"]     = round(session["thumb_mid"]["bend"], 4)
        saved.append("thumb_mid")
    if "thumb_curl_min" in session:
        calib["thumb_curl_close"]   = round(session["thumb_curl_min"], 4); saved.append("thumb_curl_min")

    if "index_pinch" in session:
        p = session["index_pinch"]
        angles = p["angles"]   # 6 target angles when pinching
        # Per-DOF EMA alpha proportional to travel from open(1000): the DOFs that
        # move most during a pinch (thumb) get the highest alpha so they keep
        # pace with the index instead of lagging. Others stay at BASE.
        BASE, AMAX = 0.5, 0.85
        travel = [abs(1000 - a) for a in angles]
        mx = max(travel) or 1.0
        calib["pinch_alpha"] = [round(BASE + (t / mx) * (AMAX - BASE), 3) for t in travel]
        calib["pinch_threshold"] = round(p["dist"] * 1.2, 4)
        saved.append("index_pinch")

    CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
    if log:
        log.info("saved calibration steps: %s", saved)
    return saved


# --- drawing helpers ---

def _put(frame, text, y, color=(255, 255, 255), scale=0.7, thick=2, x=30):
    cv2.putText(frame, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick)


def _put_banner(frame, text, color):
    w = frame.shape[1]
    bar = frame.copy()
    cv2.rectangle(bar, (0, 0), (w, 80), (0, 0, 0), -1)
    cv2.addWeighted(bar, 0.6, frame, 0.4, 0, frame)
    cv2.putText(frame, text, (30, 52), cv2.FONT_HERSHEY_SIMPLEX, 0.95, color, 2)


def _draw_menu(frame, session: dict):
    h, w = frame.shape[:2]
    ov = frame.copy()
    cv2.rectangle(ov, (0, 0), (w, h), (0, 0, 0), -1)
    cv2.addWeighted(ov, 0.55, frame, 0.45, 0, frame)
    _put(frame, "CALIBRATION MENU", 70, (0, 255, 255), 1.1, 3)
    y = 140
    for i, (key, instr, _, _) in enumerate(STEPS):
        done = key in session
        col  = (0, 255, 0) if done else (220, 220, 220)
        mark = "[OK]" if done else "[  ]"
        _put(frame, f"{mark}  {instr}", y, col, 0.7)
        y += 40
    y += 20
    _put(frame, "1-5 = run a step    A = run all", y, (255, 255, 0), 0.7); y += 36
    _put(frame, "S = save calibrated steps    Q = quit", y, (255, 255, 0), 0.7)


def run_calibration(source: FrameSource, tracker: HandTracker, win: str,
                    mirror: bool = True, swap_hands: bool = True, log=None,
                    calib: Calibration | None = None) -> bool:
    """Menu-driven calibration on an already-open camera/window.

    Returns True if anything was saved this session, else False.
    """
    if calib is None:
        calib = Calibration()
    session: dict[str, object] = {}
    saved_any = False
    phase   = "menu"
    queue: list[int] = []
    step_i  = 0
    held    = 0.0
    buf: deque = deque(maxlen=AVG_FRAMES)
    flash   = ""
    flash_t = 0.0
    prev    = time.monotonic()

    while True:
        now = time.monotonic(); dt = now - prev; prev = now
        ok, frame = source.read()
        if not ok:
            return saved_any
        if mirror:
            frame = cv2.flip(frame, 1)
        result = tracker.process(frame)
        draw_landmarks(frame, result)
        h, w = frame.shape[:2]
        has_hand = result.num_hands > 0

        left = False
        if has_hand and result.handedness:
            side = result.handedness[0].lower()
            if swap_hands:
                side = "right" if side == "left" else "left"
            left = (side == "left")

        kp = cv2.waitKey(1) & 0xFF

        if phase == "menu":
            _draw_menu(frame, session)
            if flash and now - flash_t < 2.0:
                _put(frame, flash, h - 40, (0, 255, 0), 0.8)
            if ord("1") <= kp <= ord(str(len(STEPS))):
                step_i = kp - ord("1"); phase = "ready"
            elif kp in (ord("a"), ord("A")):
                queue = list(range(len(STEPS)))
                step_i = queue.pop(0); phase = "ready"
            elif kp in (ord("s"), ord("S")):
                if session:
                    keys = _save_session(session, log)
                    saved_any = True
                    flash = f"saved: {', '.join(keys)}"; flash_t = now
                else:
                    flash = "nothing calibrated yet"; flash_t = now
            elif kp in (ord("q"), ord("Q")):
                return saved_any
            cv2.imshow(win, frame)
            continue

        key, instr, kind, agg = STEPS[step_i]

        if phase == "ready":
            _put_banner(frame, instr, (0, 200, 255))
            _put(frame, "Get in position, then press  [ SPACE ]", h // 2, (255, 255, 0), 1.0)
            _put(frame, "(3s hold while it samples)   Q = back to menu", h // 2 + 45,
                 (180, 180, 180), 0.6, 1)
            if kp == ord(" "):
                phase = "hold"; held = 0.0; buf.clear()
            elif kp in (ord("q"), ord("Q")):
                queue.clear(); phase = "menu"

        elif phase == "hold":
            if has_hand:
                lm = result.landmarks[0]
                if kind == "pinch":
                    rot_side = "left" if left else "right"
                    ang, _ = landmarks_to_angles(lm, calib, hand_side=rot_side)
                    buf.append({"angles": ang, "dist": _pinch_dist(lm)})
                    # red dots on thumb+index tips
                    for tip in (4, 8):
                        cv2.circle(frame, (int(lm[tip][0]*w), int(lm[tip][1]*h)),
                                   9, (0, 0, 255), -1)
                else:
                    buf.append(_capture(kind, lm, left))
                held += dt
            else:
                held = max(0.0, held - dt)
            _put_banner(frame, instr, (0, 200, 255))
            if not has_hand:
                _put(frame, "Show your hand...", h // 2, (0, 0, 255), 1.0)
            else:
                _put(frame, f"HOLD... {max(0.0, HOLD_SECONDS - held):0.1f}s",
                     h // 2, (0, 255, 0), 1.4, 3)
            if held >= HOLD_SECONDS and len(buf) >= 4:
                session[key] = _aggregate(buf, kind, agg)
                if log:
                    log.info("captured %s = %s", key, session[key])
                flash = f"captured {key}"; flash_t = now
                buf.clear(); held = 0.0
                if queue:
                    step_i = queue.pop(0); phase = "ready"
                else:
                    phase = "menu"

        cv2.imshow(win, frame)


def main() -> int:
    ap = argparse.ArgumentParser(description="Guided Inspire hand-range calibration")
    ap.add_argument("--source", default="9")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--no-swap-hands", dest="swap_hands", action="store_false")
    ap.set_defaults(swap_hands=True)
    args = ap.parse_args()

    log = get_logger("calibrate_hand", log_dir=LOG_DIR)
    source = FrameSource(args.source, width=1920, height=1080)
    if not source.opened():
        log.error("cannot open source %s", args.source); return 1
    tracker = HandTracker(str(args.model), num_hands=1,
                          delegate="gpu" if args.gpu else "cpu")

    WIN = "Hand Range Calibration"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
    print("Menu: 1-6 run step, A all, S save, Q quit")

    cfg = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
    calib = Calibration.from_dict(cfg.get("calibration", {}))

    try:
        saved = run_calibration(source, tracker, WIN, args.mirror, args.swap_hands,
                                log, calib=calib)
        print("saved" if saved else "nothing saved")
    finally:
        tracker.close(); source.release(); cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
