# Progress Log

## 2026-06-18

Scaffolded the project and the shared `dexkit` library.

**Completed:**

- Created shared library `300-software/60-libs/dexkit/` with `dexkit.hands`
  (Inspire RH56 RS-485 driver moved from `inspire-hand`), `dexkit.perception`
  (new MediaPipe `HandTracker` + `draw_landmarks`), `dexkit.mapping` (new
  landmark→6-DOF retargeting + EMA smoother), and `dexkit.get_logger`.
- Migrated `inspire-hand` to import the driver from `dexkit.hands` (no duplicate).
- App scripts: `src/camera.py`, `src/recognize.py` (joints overlay),
  `src/control.py` (RS-485 gestures, `--dry-run`), `src/mimic.py` (end-to-end).
- `config/hand_mimic.yaml` (mapping calibration), `requirements.txt`, `setup.sh`
  (Jetson system-python install + model download), `logs/` folder with
  connection/error logging in every script.

**Blockers:**

- Hardware not connected yet — live camera tracking and hand motion untested.

**Next:**

- On the Jetson: run `./setup.sh`, verify camera + MediaPipe, connect the
  Inspire hand over USB-RS485, then calibrate the mapping bounds in
  `config/hand_mimic.yaml`.

---

<!-- Add new entries above this line, newest first -->
