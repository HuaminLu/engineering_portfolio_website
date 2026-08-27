# Hand Mimic — Real-Time Camera-to-Robot-Hand Teleoperation

Hold your hand in front of a webcam and the Inspire RH56 dexterous hand mirrors every finger
movement live, with ~30 ms end-to-end latency. The camera window shows a skeleton overlay of
the tracked landmarks and the joint angles being sent to hardware.

## How to run

```bash
./run.sh          # uses the project .venv (mediapipe, dexkit live there — system python will ModuleNotFoundError)
```

Press `t` during operation to hot-reload `config/hand_mimic.yaml` without restarting —
that's how the hand was calibrated iteratively against real hardware.

## Architecture

```
webcam → MediaPipe HandLandmarker (21 3-D landmarks, 25–30 Hz, GPU delegate)
       → geometric retargeting (retarget.py)     — landmark triplets → joint angles
       → EMA smoothing + occlusion guard
       → InspireHand.set_angles() over RS-485 @ 115200 baud → hand firmware servo loops
```

### Retargeting (the core algorithm)

Each finger uses a triplet of MediaPipe landmarks (MCP, PIP, tip) and computes the interior
angle at the PIP vertex:

```python
_FINGER_TRIPLETS = {"index": (5, 6, 8), "middle": (9, 10, 12),
                    "ring": (13, 14, 16), "pinky": (17, 18, 20)}

def _angle_deg(a, b, c):
    v1, v2 = a - b, c - b
    return degrees(arccos(clip(dot(v1, v2) / (norm(v1) * norm(v2)), -1, 1)))
```

~170° (extended) maps to 1000 on the Inspire's 0–1000 register scale; ~50° (curled) maps to 0.

### Thumb — a two-layer system

1. **Pinch detection** — thumb-tip↔index-tip distance normalized by palm width (scale-invariant).
   Below `pinch_threshold: 0.28` palm-widths, a hardware preset snaps the thumb to the pinch pose
   instantly instead of going through geometry.
2. **Thumb curl** — uses the IP-joint angle (CMC→MCP→IP), which stays stable when the thumb folds
   across the palm (tip-to-palm distance does not).
3. **Thumb rotation** — normalized x-offset of the thumb tip from the index MCP.

### Robustness

- **Occlusion guard**: inside a fist, MediaPipe reports a false ~180° "open" angle for hidden
  fingertips. If a fingertip is within 0.6 palm-widths of the palm center, output clamps to
  near-closed regardless of the computed angle.
- **EMA smoothing**: `state = α·measurement + (1−α)·state`, per-DOF α, with a 6× wider deadband
  on the thumb channels (the noisiest axes).
- **Three retargeting backends**: `geom` (fast calibrated mapping, default), `dex`
  (dex-retargeting optimizer, higher quality), `hybrid` (geometric everywhere, optimizer takes
  over index+thumb during a detected pinch).

### Gesture overlay

Colored fingertip circles in the CV window: orange = fist, blue = 3-finger pinch,
red = 2-finger pinch.

## Hardware

- Inspire RH56 hand, 24 V, 6 actuated DOF, Modbus-style register protocol over RS-485
- USB-to-RS485 dongle (FTDI/CH340) at `/dev/ttyUSB0` / `/dev/ttyUSB1` for left/right hands
- GX12 4-pin aviation connector carries RS-485 A+/B− and 24 V to the hand
- The hand firmware runs the per-servo PD loops — the host only writes angle registers:

```python
hand = InspireHand(port="/dev/ttyUSB0", baud=115200, hand_id=1)
hand.set_speed([2000] * 6); hand.set_force([400] * 6)
hand.set_angles([pinky, ring, middle, index, thumb_bend, thumb_rot], wait=False)
```

## How it was made

Built iteratively against real hardware: the geometric mapping came first, then calibration
bounds (`curl_open_deg: 170`, `curl_closed_deg: 50`, thumb distance bounds) were tuned live
using the `t` hot-reload. The pinch preset and occlusion guard were added after testing showed
geometry alone fails exactly when the hand closes — the most important poses. The hybrid
backend came last, combining the geometric path's speed with the optimizer's precision where
it matters.

## Key files

- `current/mimic.py` — main loop: capture → track → retarget → smooth → send
- `current/retarget.py` — landmark triplets, angle math, thumb logic, occlusion guard
- `current/dex_backend.py` — dex-retargeting optimizer backend
- `current/hand_mimic.yaml` + `config/` — calibration values, MediaPipe model
- `run.sh` / `setup.sh` — venv bootstrap and launch
