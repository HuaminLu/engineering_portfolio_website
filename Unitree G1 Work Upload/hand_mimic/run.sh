#!/bin/bash
# hand_mimic — known-good demo launcher.
#
# CURRENT VERSION = hybrid-v2 (snapshots/hybrid-v2), dedicated runner
# src/mimic_hybrid.py: calibrated geometric (4 fingers) + DexPilot pinch
# override (index + thumb_bend + thumb_rot only during a pinch).
#
# Camera: Logitech Brio 305 USB webcam at /dev/video8 (index 8). The runner
# defaults to --width 1920 --height 1080 (the Brio's MAX resolution), and
# _source.py forces MJPG so 1080p runs at full FPS. The frame is captured at
# native max res (no blurry pre-upscale); OpenCV scales it into the fullscreen
# window. index 0-7 is the laptop's IPU6 MIPI cam (unreadable by OpenCV).
#
# --config config/hand_mimic.yaml is loaded by default and carries the correct
# degree-unit thumb-curl bounds so THUMB BEND moves (not just rotation).
#
# Fullscreen: patched to defer WND_PROP_FULLSCREEN until after the first frame
# and to resize/move the window to the detected screen size, since GNOME/mutter
# ignores the fullscreen property on its own.
#
# --right-port none  -> left hand only (single-hand demo).
# Extra flags pass straight through, e.g.:  ./run.sh --windowed  |  ./run.sh --dry-run
set -e
cd "$(dirname "$0")"
exec .venv/bin/python src/mimic_hybrid.py \
    --source 8 \
    --mirror \
    --gpu \
    --config config/hand_mimic.yaml \
    "$@"
