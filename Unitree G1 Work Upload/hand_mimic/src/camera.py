#!/usr/bin/env python3
"""Camera test for hand_mimic — open a camera, show the feed + FPS.

    python src/camera.py                 # open index 0
    python src/camera.py --index 2 --width 1280 --height 720
    python src/camera.py --probe         # list /dev/video* devices

Connections and errors are logged to logs/camera.log.
"""

from __future__ import annotations

import argparse
import glob
import time
from pathlib import Path

import cv2

from dexkit import get_logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_ROOT / "logs"


def probe() -> list[str]:
    devices = sorted(glob.glob("/dev/video*"))
    for d in devices:
        print(d)
    if not devices:
        print("no /dev/video* devices found")
    return devices


def main() -> int:
    ap = argparse.ArgumentParser(description="hand_mimic camera test")
    ap.add_argument("--index", type=int, default=0, help="camera index")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--probe", action="store_true", help="list cameras and exit")
    args = ap.parse_args()

    log = get_logger("camera", log_dir=LOG_DIR)

    if args.probe:
        probe()
        return 0

    cap = cv2.VideoCapture(args.index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        log.error("cannot open camera index=%d", args.index)
        return 1
    log.info(
        "camera opened index=%d %dx%d",
        args.index,
        int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )

    frames, t0, fps = 0, time.time(), 0.0
    print("Press 'q' to quit")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                log.error("frame read failed")
                break
            frames += 1
            elapsed = time.time() - t0
            if elapsed >= 1.0:
                fps = frames / elapsed
                frames, t0 = 0, time.time()
            cv2.putText(frame, f"FPS: {fps:.1f}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
            cv2.imshow("hand_mimic camera", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except Exception:
        log.exception("camera loop crashed")
        return 1
    finally:
        cap.release()
        cv2.destroyAllWindows()
        log.info("camera closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
