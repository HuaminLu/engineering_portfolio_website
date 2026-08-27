#!/usr/bin/env python3
"""Thumb diagnostic — run with your hand in frame.

Make a FIST, hold 2s, then OPEN HAND, hold 2s, repeat.
Shows the raw geometric values going into thumb_bend and thumb_rot
so we can see what's actually being measured vs what the Inspire gets.

    uv run python src/thumb_diag.py --source 8 --mirror
"""
import argparse, time
from pathlib import Path

import cv2, numpy as np

from _source import FrameSource
from dexkit.mapping.retarget import (
    _angle_deg, _palm_size, _thumb_curl, _thumb_tilt,
    landmarks_to_angles, Calibration, DEFAULT_CALIB
)
from dexkit.perception import HandTracker, draw_landmarks

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="8")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--mirror", action="store_true")
    ap.add_argument("--gpu", action="store_true")
    args = ap.parse_args()

    source = FrameSource(args.source, width=1920, height=1080)
    tracker = HandTracker(str(args.model), num_hands=1,
                          delegate="gpu" if args.gpu else "cpu")
    calib = DEFAULT_CALIB

    WIN = "Thumb diagnostic — FIST then OPEN HAND"
    cv2.namedWindow(WIN, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WIN, 1280, 720)
    print("Show your hand — make a fist, then open. Press Q to quit.")

    while True:
        ok, frame = source.read()
        if not ok: break
        if args.mirror: frame = cv2.flip(frame, 1)
        result = tracker.process(frame)
        draw_landmarks(frame, result)

        if result.num_hands:
            lm = result.landmarks[0]
            palm = _palm_size(lm)
            palm_c = (lm[5] + lm[17]) / 2.0

            # --- curl measurement (tip-to-palm-centre distance) ---
            dist_to_palm = float(np.linalg.norm(lm[4] - palm_c)) / palm
            bend_out = _thumb_curl(lm, calib)

            # --- rotation: x-offset of tip from index MCP ---
            tip_x_offset = float(lm[4][0] - lm[5][0]) / palm
            rot_out = _thumb_tilt(lm, calib)

            all_angles, pinch_mode = landmarks_to_angles(lm, calib)

            y, lh = 40, 32
            def put(txt, col=(0,255,255)):
                nonlocal y
                cv2.putText(frame, txt, (20, y),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
                y += lh

            put(f"CURL  dist_to_palm={dist_to_palm:.2f}  bend_out={bend_out}", (0,255,0))
            put(f"  range: far={calib.thumb_curl_far:.2f} close={calib.thumb_curl_close:.2f}")
            put(f"ROT   tip_x_offset={tip_x_offset:.2f}  rot_out={rot_out}", (0,200,255))
            put(f"  range: tucked={calib.thumb_rot_x_tucked:.2f} spread={calib.thumb_rot_x_spread:.2f}")
            put(f"ALL: pinky={all_angles[0]} ring={all_angles[1]} mid={all_angles[2]} "
                f"idx={all_angles[3]} bend={all_angles[4]} rot={all_angles[5]} "
                f"{'[PINCH:'+pinch_mode+']' if pinch_mode else ''}", (255,200,0))

            print(f"dist={dist_to_palm:.2f} bend={bend_out} | x_off={tip_x_offset:.2f} rot={rot_out}")
        else:
            cv2.putText(frame, "No hand detected", (20, 40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,0,255), 2)

        cv2.imshow(WIN, frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    tracker.close(); source.release(); cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
