# Arm Recorder GUI — 14-DOF Joint-Space Trajectory Animator

A Flet desktop GUI (yellow/black theme) to dial, record, and replay precise multi-joint arm
trajectories on the G1's two 7-DOF arms. Unlike the hand tools (serial registers), this
streams a full `LowCmd_` packet over the `rt/arm_sdk` DDS topic at 50 Hz with
position / velocity / torque / stiffness control for all 14 arm joints simultaneously.
Recordings become training data for a continuous arm policy (`build_preset_policy.py`).

## How to run

```bash
python3 arm_anim_recorder.py     # system python3 (flet 0.86); auto-detects the G1 interface
```

The G1's wired Ethernet port must be up on the `192.168.123.x` network, or the GUI shows
"no DDS / not connected" until it is. The vendored `unitree_sdk2_python`, `librealsense`,
and Livox SDK folders were stripped from this upload — `pip install unitree_sdk2py` to run.

## Joint architecture

14 arm joints (7 per arm), Unitree motor IDs 15–21 (left) and 22–28 (right):
Shoulder Pitch / Shoulder Roll / Shoulder Yaw / Elbow / Wrist Roll / Wrist Pitch / Wrist Yaw.

The 3 waist joints (IDs 12–14) are locked upright at `q=0` with **kp=200, kd=5** so the torso
doesn't droop when `arm_sdk` takes control of the upper body.

## How it works

**50 Hz publish loop:**

```python
KP_STIFF, KD_STIFF = 60.0, 1.5        # arm position gains
msg.motor_cmd[29].q = 1.0             # arm_sdk enable flag, set every packet
for i, idx in enumerate(LEFT_IDX):    # [15..21]
    msg.motor_cmd[idx].q  = cmd_q[i]  # ramped toward the slider target
    msg.motor_cmd[idx].kp = KP_STIFF
    msg.motor_cmd[idx].kd = KD_STIFF
```

Safety and ergonomics features, each earned by a real incident or need:

- **Ramp limiter** — commanded position ramps toward the slider target each tick instead of
  jumping, preventing torque spikes on large slider moves.
- **Startup seeding** — subscribes to `rt/lowstate` (500 Hz state feedback) before publishing
  and seeds the sliders from the arm's *measured* pose, so connecting never snaps the arm to zero.
- **Damp mode** — kp=kd=0 makes an arm limp so you can physically pose it by hand.
- **Mirror mode** — left-arm recordings mirror to the right arm; axes {shoulder roll, shoulder
  yaw, wrist roll, wrist yaw} flip sign for kinematic chirality (`MIRROR_FLIP = {1, 2, 4, 6}`).
- **NIC auto-detection** — scans `/sys/class/net` for a `192.168.123.x` interface, so different
  USB-Ethernet dongles work without hardcoding names.

**Trajectory files** (`data/trajectories/`):

```yaml
gesture: up
fps: 25
frames:
  left:
    - [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]     # home
    - [0.1, 0.05, 0.0, -0.1, 0.0, 0.2, 0.0]
```

## From recordings to a continuous policy

`build_preset_policy.py` turns the 6 direction presets (up/down/left/right/forward/back) into
training data for an MLP mapping a **3-axis command vector** → **joint-space delta from home**:

```
command = (k / (N−1)) · axis_unit(direction)  →  frame_k − home_pose
```

```python
model = MLPRegressor(hidden_layer_sizes=(32, 32), activation="relu",
                     solver="adam", max_iter=400)
# saved with scalers + joint limits to arm_policy.joblib
```

The proportional labeling gives the MLP magnitude — `(0.5, 0, 0)` means "50% of the way to
the forward preset" — and it interpolates between axes for blended commands like `(0.7, 0, 0.3)`.
The arm reaches any pose *between* the six recorded presets, not just the six.

## How it was made

Direct port of the Hand Recorder concept up one control plane: sliders → takes → presets, but
against DDS `LowCmd_` instead of serial registers. The waist-lock, ramp limiter, and lowstate
seeding were all added after early tests (drooping torso, torque snaps on connect). The
policy-training step came last, once a library of clean directional recordings existed.

## Key files

- `arm_anim_recorder.py` — the recorder GUI (sliders, takes, DDS streaming, mirror/damp modes)
- `arm_gui.py`, `keyboard_controller.py` — related control front-ends
- `data/` — recorded trajectories and training CSVs
- `docs/`, `FSM_README.md`, `fsm_cheatsheet.html` — G1 FSM state notes gathered while debugging
