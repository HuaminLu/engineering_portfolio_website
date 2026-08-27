# Project: Hand Mimic

**Status:** :large_blue_circle: In Progress
**Created:** 2026-06-18

## Overview

Inspire RH56 dexterous hands on a table mimicking human hand gestures in real
time. A camera on a standalone **Jetson Orin** feeds Google MediaPipe hand
tracking; the detected 21 finger joints are retargeted to the hand's 6 DOF and
streamed to the Inspire hand over RS-485. The camera view shows the tracked
joints — suitable for the demo GIF.

This project is intentionally thin: hardware driver, perception, and retargeting
all live in the shared **`dexkit`** library
(`300-software/60-libs/dexkit/`); the scripts here are the application glue.

## Goals

| # | Goal | Measure | Status |
|---|------|---------|--------|
| 1 | MediaPipe hand tracking on Jetson | joints drawn on live feed | :white_circle: |
| 2 | Camera / recognition / control scripts | each runs standalone | :large_blue_circle: |
| 3 | Inspire hand over RS-485 (USB adapter) | gestures execute on hardware | :white_circle: |
| 4 | End-to-end mimicry | hand follows human in real time | :white_circle: |

## Hardware

- **Camera:** USB / CSI camera on the Jetson (`python3 src/camera.py --probe`).
- **Inspire RH56** dexterous hand, **RS-485** (standard interface, per the RH56
  manual §1.4.2 — CAN is a non-standard variant with no documented frame spec).
- **USB-to-RS485 adapter** → `/dev/ttyUSB0`, 115200 8N1.
- **GX12 5-pin plug:** 1 GND · 2 VCC (24 V) · 3 A+ · 4 B− · 5 GND.
  Supply the hand with 24 V; A+/B− to the adapter.

## Setup

Jetson Orin (system python3):

```bash
./setup.sh
```

Workstation (x86, for development) — a `.venv` lives in this folder:

```bash
uv venv --python 3.10
uv pip install -r requirements.txt   # opencv-python ok here; skip on Jetson
# fetch the MediaPipe model once:
curl -fSL https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task \
  -o config/hand_landmarker.task
```

`setup.sh` does the equivalent on the Jetson and downloads the model to
`config/hand_landmarker.task` (not committed). Prefix run commands with
`uv run` on the workstation (e.g. `uv run python src/recognize.py`).

## Run

The vision scripts take `--source`, which accepts a **camera index, a video
file, or an image** — so you can test without a live camera:

```bash
python3 src/camera.py --probe         # list cameras
python3 src/camera.py                  # live preview + FPS

# live camera (index)
python3 src/recognize.py --source 0    # joints overlay + angle readout
# test with no camera (image or video file)
python3 src/recognize.py --source artifacts/sample_hands.jpg
# headless (no GUI window), dump annotated frames for a GIF
python3 src/recognize.py --source clip.mp4 --no-window --record

python3 src/control.py open            # named gesture on the hand
python3 src/control.py open --dry-run  # print target angles, no serial

python3 src/mimic.py --source 0                 # end-to-end: human -> Inspire hand
python3 src/mimic.py --source clip.mp4 --dry-run # perception only, no hardware
```

Pass `--config config/hand_mimic.yaml` to `recognize.py` / `mimic.py` to use
tuned mapping calibration.

> **Workstation camera note:** this dev laptop has an **Intel IPU6 MIPI** webcam
> (`/dev/video*` = `ipu6-downstream`). OpenCV/V4L2 cannot read it directly
> without the Intel IPU6 + libcamera relay stack, so `--source 0` yields no
> frames here. Use `--source <image|video>` to test the pipeline, or plug in a
> USB webcam (it will enumerate as a normal index). The full software stack
> (MediaPipe + retargeting) is verified working on sample input.

## Joint convention

Order `[pinky, ring, middle, index, thumb_bend, thumb_rot]`; angle `0` =
closed/bent, `1000` = open (`-1` = no action). Mapping bounds are tunable in
`config/hand_mimic.yaml` (`calibration:`) → `dexkit.mapping.Calibration`.

## Logs

All scripts write connection events and errors to `logs/<script>.log` (rotating).
Check these first when debugging hardware issues.

## Troubleshooting

- **No `/dev/video*`:** check camera cable; `v4l2-ctl --list-devices`.
- **No `/dev/ttyUSB0`:** check the USB-RS485 adapter; `dmesg | tail`; add your
  user to the `dialout` group for port access.
- **Hand doesn't move:** confirm 24 V supply and A+/B− polarity; try
  `python3 src/control.py open` and read `logs/control.log`.
- **Model missing:** re-run `./setup.sh` (downloads `hand_landmarker.task`).

## Links

- [dexkit shared library](../../../300-software/60-libs/dexkit/README.md)
- [Inspire hand controller (sibling project)](../inspire-hand/README.md)
- `Inspire Hands.pdf` — RH56 Series User Manual (RS-485/Modbus protocol)
