#!/usr/bin/env python3
"""Hand recognition view — camera + MediaPipe joints overlay.

Shows the live camera with the 21 detected hand joints drawn on top, plus
handedness and the retargeted 6-DOF angle readout. This is the "camera view
showing the joints" deliverable.

    python src/recognize.py
    python src/recognize.py --index 2 --num-hands 1
    python src/recognize.py --record      # dump annotated frames to artifacts/

Connections and errors are logged to logs/recognize.log.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from _config import load_calibration
from _source import FrameSource

from dexkit import get_logger
from dexkit.mapping import landmarks_to_angles
from dexkit.perception import HandTracker, draw_landmarks

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"
ARTIFACTS = PROJECT_ROOT / "artifacts"


def main() -> int:
    ap = argparse.ArgumentParser(description="hand_mimic recognition view")
    ap.add_argument("--source", default="0",
                    help="camera index, video file, or image path")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL,
                    help="path to hand_landmarker.task")
    ap.add_argument("--num-hands", type=int, default=2)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--config", type=Path, default=None,
                    help="YAML with a 'calibration:' section for the mapping")
    ap.add_argument("--no-window", action="store_true",
                    help="headless: don't call cv2.imshow (use with --record)")
    ap.add_argument("--max-frames", type=int, default=300,
                    help="stop after N frames in --no-window mode")
    ap.add_argument("--record", action="store_true",
                    help="save annotated frames to artifacts/ for GIF assembly")
    args = ap.parse_args()

    log = get_logger("recognize", log_dir=LOG_DIR)
    calib = load_calibration(args.config, log)

    if not args.model.exists():
        log.error("model not found: %s (run setup.sh to download it)", args.model)
        return 1

    try:
        source = FrameSource(args.source, width=args.width, height=args.height)
    except Exception:
        log.exception("cannot open source %s", args.source)
        return 1
    if not source.opened():
        log.error("cannot open source %s", source.describe())
        return 1
    log.info("source opened %s", source.describe())

    try:
        tracker = HandTracker(str(args.model), num_hands=args.num_hands)
        log.info("hand tracker loaded model=%s num_hands=%d", args.model, args.num_hands)
    except Exception:
        log.exception("failed to load hand tracker")
        source.release()
        return 1

    rec_dir = None
    if args.record:
        rec_dir = ARTIFACTS / time.strftime("rec_%Y%m%d_%H%M%S")
        rec_dir.mkdir(parents=True, exist_ok=True)
        log.info("recording frames to %s", rec_dir)

    frame_i = 0
    if not args.no_window:
        print("Press 'q' to quit")
    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed")
                break
            result = tracker.process(frame)
            draw_landmarks(frame, result)

            for h, lm in enumerate(result.landmarks):
                try:
                    angles, _ = landmarks_to_angles(lm, calib)
                except Exception:
                    log.exception("retarget failed (hand %d)", h)
                    continue
                cv2.putText(frame, f"{result.handedness[h]}: {angles}",
                            (10, 30 + 24 * h), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (0, 255, 255), 1)

            if rec_dir is not None:
                cv2.imwrite(str(rec_dir / f"frame_{frame_i:05d}.png"), frame)
            frame_i += 1

            if args.no_window:
                if frame_i >= args.max_frames:
                    break
            else:
                cv2.imshow("hand_mimic recognize", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except Exception:
        log.exception("recognize loop crashed")
        return 1
    finally:
        tracker.close()
        source.release()
        cv2.destroyAllWindows()
        log.info("recognize closed (%d frames)", frame_i)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
