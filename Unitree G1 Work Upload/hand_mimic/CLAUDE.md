# hand_mimic — quickstart for new sessions

Runbook for getting the hand-mimicry demo running fast. For the full project
description, hardware wiring, and troubleshooting, see [`README.md`](README.md).

**What it does:** human hand in front of a camera → Google MediaPipe hand
tracking → retarget to 6 DOF → Inspire RH56 dexterous hand moves in real time.

**Where it runs:** a **standalone Jetson Orin** (not the G1). Camera + USB-RS485
adapter + the hand sit on the bench.

## Golden rules

- **Jetson = system python3 only.** Never `uv`/`conda`/`pyenv` here (breaks
  CUDA/TensorRT wheels). Install via `pip3 --extra-index-url
  https://pypi.jetson-ai-lab.io/jp6`. On the **x86 workstation** for dev, use `uv`.
- **This project depends on `dexkit`** (`../../../300-software/60-libs/dexkit/`).
  `setup.sh` installs it editable; nothing runs without it on `PYTHONPATH`.
- **Check `logs/` first when debugging** — every script logs connections and
  errors to `logs/<script>.log`.

## Environments to open

1. A terminal **on the Jetson** (`ssh` in, or local). All commands below run there.
2. A **display** for the OpenCV preview windows (`recognize.py`, `mimic.py`,
   `camera.py` call `cv2.imshow`). Either a monitor on the Jetson or X11
   forwarding: `ssh -X orin`. Headless? add a `--record`/no-window path or run
   with a virtual display — the windows will fail without one.
3. (Optional) a second terminal tailing logs: `tail -f logs/mimic.log`.

## First-time setup (once per machine)

```bash
cd 000-projects/active/hand_mimic
./setup.sh        # system pip install (jetson-ai-lab), downloads the MediaPipe
                  # model to config/hand_landmarker.task, editable-installs dexkit
```

Workstation dev instead of Jetson (a `.venv` lives in this folder):

```bash
uv venv --python 3.10
uv pip install -r requirements.txt    # opencv-python ok here; skip on Jetson
curl -fSL https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task \
  -o config/hand_landmarker.task
```

Then prefix runs with `uv run` (e.g. `uv run python src/recognize.py ...`).

**Workstation camera caveat:** this laptop's webcam is **Intel IPU6 MIPI**
(`/dev/video*` = `ipu6-downstream`); OpenCV can't read it without the Intel
IPU6/libcamera relay. Test with `--source <image|video>` or a USB webcam. The
vision stack itself is verified on sample input.

## Bring-up sequence (run in this order)

Start with no hardware needed, then add the camera, then the hand. Stop at the
first step that fails and check the matching log.

On the workstation, prefix each with `uv run`. The vision scripts take
`--source` = camera index | video file | image (default `0`).

```bash
# 1. Camera present?            (needs: camera)
python3 src/camera.py --probe          # lists /dev/video*
python3 src/camera.py                  # live preview + FPS, 'q' to quit

# 2. Hand tracking works?       (needs: model; camera OR a test image/video)
python3 src/recognize.py --source 0                       # live camera
python3 src/recognize.py --source artifacts/sample_hands.jpg   # no camera needed
#   -> sanity-check the printed angles change as you open/close your hand

# 3. Hand reachable, no motion? (needs: nothing — safe)
python3 src/control.py open --dry-run  # prints target angles, opens no serial

# 4. Hand moves?                (needs: Inspire hand on /dev/ttyUSB0, 24 V)
python3 src/control.py open            # then: close / pinch / point / peace / thumbs_up
#   -> if nothing moves, read logs/control.log (port? polarity? 24 V?)

# 5. Full demo                  (needs: camera + hand)
python3 src/mimic.py --source 0                  # human hand drives the Inspire hand
python3 src/mimic.py --source clip.mp4 --dry-run # perception only, no hardware
```

Headless (no display)? add `--no-window` (and `--record` on recognize.py) to
run the pipeline and dump annotated frames to `artifacts/` instead of opening a
window.

## Calibrate the mapping

The geometric landmark→angle bounds are first-pass. Tune them against the real
hand, then pass the config to `recognize.py` / `mimic.py`:

```bash
# edit config/hand_mimic.yaml  (calibration: section)
python3 src/recognize.py --config config/hand_mimic.yaml
python3 src/mimic.py     --config config/hand_mimic.yaml
```

## Capture the demo GIF

```bash
python3 src/recognize.py --record      # annotated frames -> artifacts/rec_<ts>/
# then assemble (ffmpeg/imageio), e.g.:
#   ffmpeg -framerate 20 -i artifacts/rec_*/frame_%05d.png artifacts/hand_mimic.gif
```

## Common gotchas

- `cv2.imshow` errors → no display; use `ssh -X` or a local monitor.
- `/dev/ttyUSB0` permission denied → add user to `dialout`, re-login.
- Hand silent but no error → check 24 V supply and A+/B− polarity (GX12 pinout
  in README); confirm `--hand-id` matches the hand.
- `model not found` → re-run `./setup.sh`.
- Import errors for `dexkit` → it isn't installed/on path; re-run `setup.sh` or
  `export PYTHONPATH=../../../300-software/60-libs/dexkit`.

## Layout

| Path | What |
|------|------|
| `src/camera.py` | camera probe + preview |
| `src/recognize.py` | hand tracking + joints overlay (the "show joints" view) |
| `src/control.py` | RS-485 gestures (`--dry-run` safe) |
| `src/mimic.py` | end-to-end camera→hand |
| `src/_config.py` | shared YAML calibration loader |
| `src/_source.py` | frame source (camera index / video / image) |
| `config/hand_mimic.yaml` | camera/serial defaults + mapping calibration |
| `logs/` | per-script connection + error logs |
| `artifacts/` | recorded frames / outputs |
| `../../../300-software/60-libs/dexkit/` | shared driver + perception + mapping |
