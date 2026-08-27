#!/usr/bin/env python3
"""Human-hand geometry calibration.

Since the Inspire hand can't be backdriven, we calibrate the INPUT side:
record what the MediaPipe geometry looks like at each extreme position on
YOUR hand, then use those as the mapping bounds.

Steps:
  1. Hold your hand FULLY OPEN (thumb extended, spread out)  → press O
  2. Make a tight FIST (thumb tucked across palm)            → press F
  3. Thumb fully SPREAD away from hand (abducted)           → press A (rotation open)
  4. Thumb fully TUCKED alongside index finger               → press T (rotation closed)
  5. Press W to write bounds to config/hand_mimic.yaml
  6. Press Q to quit

The tool averages 10 frames when you press a key for stability.
Logs to logs/calib_bounds.log.
"""

from __future__ import annotations
import argparse, time
from pathlib import Path
from collections import deque

import cv2
import numpy as np
import yaml

from _source import FrameSource
from dexkit import get_logger
from dexkit.mapping.retarget import _palm_size
from dexkit.perception import HandTracker, draw_landmarks

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
LOG_DIR       = PROJECT_ROOT / "logs"
CONFIG_PATH   = PROJECT_ROOT / "config" / "hand_mimic.yaml"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"

_CLR = dict(white=(255,255,255), yellow=(0,255,255), green=(0,255,0),
            red=(0,0,255), orange=(0,165,255), gray=(160,160,160))


def _put(frame, text, y, col=(255,255,255), scale=0.65, thick=2):
    cv2.putText(frame, text, (20, y), cv2.FONT_HERSHEY_SIMPLEX, scale, col, thick)


def _measure(lm: np.ndarray) -> tuple[float, float]:
    """Return (dist_to_palm, x_offset) for the thumb tip."""
    palm      = _palm_size(lm)
    palm_c    = (lm[5] + lm[17]) / 2.0
    dist      = float(np.linalg.norm(lm[4] - palm_c)) / palm
    x_offset  = float(lm[4][0] - lm[5][0]) / palm
    return dist, x_offset


def _avg_buf(buf: deque) -> float | None:
    return float(np.mean(buf)) if buf else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="9")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--gpu",    action="store_true")
    ap.add_argument("--frames", type=int, default=10,
                    help="frames to average per keypress")
    args = ap.parse_args()

    log    = get_logger("calib_bounds", log_dir=LOG_DIR)
    source = FrameSource(args.source, width=1920, height=1080)
    tracker = HandTracker(str(args.model), num_hands=1,
                          delegate="gpu" if args.gpu else "cpu")

    WIN = "Geometry calibration — O=open F=fist A=spread T=tuck W=write Q=quit"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WIN, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    # Circular buffers of recent measurements (updated every frame a hand is visible)
    dist_buf = deque(maxlen=args.frames)
    xoff_buf = deque(maxlen=args.frames)

    recorded: dict[str, float] = {}   # key -> value
    STATUS = ""

    KEYS = {
        "o": ("thumb_curl_far",       "dist", "OPEN hand (thumb extended)",      _CLR["green"]),
        "f": ("thumb_curl_close",     "dist", "FIST (thumb tucked across palm)",  _CLR["red"]),
        "a": ("thumb_rot_x_spread",   "xoff", "SPREAD thumb outward",             _CLR["yellow"]),
        "t": ("thumb_rot_x_tucked",   "xoff", "TUCK thumb alongside index",       _CLR["orange"]),
    }

    print("Hold each position and press the key. Press W to save, Q to quit.")

    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed"); break
            if args.mirror:
                frame = cv2.flip(frame, 1)

            result = tracker.process(frame)
            draw_landmarks(frame, result)

            if result.num_hands:
                lm = result.landmarks[0]
                dist, xoff = _measure(lm)
                dist_buf.append(dist)
                xoff_buf.append(xoff)

                # Live readout
                _put(frame, f"dist_to_palm={dist:.3f}  x_offset={xoff:+.3f}",
                     50, _CLR["white"])
            else:
                _put(frame, "No hand detected", 50, _CLR["red"])

            # Status + recorded values
            y = 100
            _put(frame, STATUS, y, _CLR["green"]); y += 34
            for ch, (cfg_key, kind, label, col) in KEYS.items():
                val = recorded.get(cfg_key)
                done = "✓" if val is not None else " "
                _put(frame, f"[{ch.upper()}] {label} → {cfg_key} = {val if val is not None else '?':}", y, col, scale=0.55); y += 28

            _put(frame, "[W] write to config   [Q] quit", y + 10, _CLR["gray"], scale=0.55)

            cv2.imshow(WIN, frame)
            key = cv2.waitKey(1) & 0xFF
            ch  = chr(key).lower() if 32 <= key < 127 else None

            if ch == "q":
                break

            elif ch in KEYS:
                cfg_key, kind, label, col = KEYS[ch]
                buf = dist_buf if kind == "dist" else xoff_buf
                if not buf:
                    STATUS = "No hand visible — show your hand then press again"
                    continue
                val = _avg_buf(buf)
                recorded[cfg_key] = round(val, 4)
                STATUS = f"Recorded {cfg_key} = {val:.4f}"
                log.info("recorded %s = %.4f (%s)", cfg_key, val, label)

            elif ch == "w":
                if len(recorded) < 2:
                    STATUS = "Record at least open (O) and fist (F) first"
                    continue
                cfg = yaml.safe_load(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}
                calib = cfg.setdefault("calibration", {})
                calib.update(recorded)
                CONFIG_PATH.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
                STATUS = f"Saved {list(recorded.keys())} to config"
                log.info("wrote bounds: %s", recorded)

                # Flash confirmation
                conf = frame.copy()
                cv2.rectangle(conf, (0,0), (conf.shape[1], 80), (0,80,0), -1)
                _put(conf, f"Saved to {CONFIG_PATH.name}", 50, _CLR["green"], scale=0.8)
                cv2.imshow(WIN, conf); cv2.waitKey(1500)

    except Exception:
        log.exception("calib_bounds crashed")
        return 1
    finally:
        tracker.close(); source.release(); cv2.destroyAllWindows()
        log.info("calib_bounds closed. recorded: %s", recorded)

    if recorded:
        print("\nRecorded bounds:")
        for k, v in recorded.items():
            print(f"  {k}: {v}")
        print(f"Written to {CONFIG_PATH}" if "w" in (ch or "") else "Not saved (press W next time)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
