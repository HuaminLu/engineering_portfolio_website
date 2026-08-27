# Tasks

## In Progress

<!-- Tasks currently being worked on -->

## To Do

- [ ] **Set up MediaPipe on the Jetson Orin**
  - Run `./setup.sh`; confirm `import mediapipe` works on system python3.
- [ ] **Verify camera capture on Jetson**
  - `python3 src/camera.py --probe` then `python3 src/camera.py`.
- [ ] **Verify hand recognition + joints overlay**
  - `python3 src/recognize.py`; confirm 21 joints draw and angle readout is sane.
- [ ] **Connect Inspire hand over USB-RS485 and communicate**
  - Wire GX12 (24 V, A+/B−); `python3 src/control.py open`; check `logs/control.log`.
- [ ] **Calibrate the landmark→angle mapping**
  - Tune `config/hand_mimic.yaml` `calibration:` bounds against the real hand.
- [ ] **Capture the demo GIF**
  - `python3 src/recognize.py --record`; assemble frames from `artifacts/` (ffmpeg/imageio).

## Blocked

- [ ] **End-to-end mimicry test** — blocked on hardware (camera + hand) being connected.

## Demo Videos to Film

- [ ] **Full hand grip on large object** — both hands closing on a large object, showing full range of motion
- [ ] **Both hands moving + MediaPipe overlay** — side-by-side or split: real hands on one side, camera overlay with MediaPipe landmarks on the other
- [ ] **One hand pinches + MediaPipe overlay** — single hand showing 2-finger and 3-finger pinch gestures with landmark overlay

## Done

- [x] Scaffold project + shared `dexkit` library; migrate inspire-hand driver. (2026-06-18)
- [x] End-to-end mimicry working on workstation (camera:8, hybrid backend, both hands). (2026-06-25)
