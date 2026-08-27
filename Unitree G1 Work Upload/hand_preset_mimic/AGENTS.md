# Hand Recorder GUI — Keyframe Gesture Animator

A Flet (Flutter-based) desktop GUI for authoring and replaying precise gesture animations on
the Inspire RH56 dexterous hand. Six live sliders map to the hand's six DOF; poses are recorded
into "takes," combined into sequences, and saved as YAML presets that replay on command.

## How to run

```bash
.venv/bin/python src/recorder.py              # blue/black theme (default)
.venv/bin/python src/recorder.py --theme bw   # black/white theme
```

Uses the project `.venv` — system python will hit `ModuleNotFoundError`.

## How it works

**Live streaming loop** — every slider value streams to the hand at 25 Hz while you drag,
so you feel the pose in hardware as you dial it:

```python
JOINT_NAMES = ["pinky", "ring", "middle", "index", "thumb_bend", "thumb_rot"]
SEND_HZ = 25
```

**Recording workflow:**

1. Adjust sliders → hand follows live
2. `REC` → samples the 6-DOF vector at 25 Hz into a Take
3. `STOP` → auto-smoothing (linear interpolation / EMA)
4. Select two Takes → `COMBINE` → parallel merge (both tracks play simultaneously)
5. `SAVE PRESET` → concatenates everything into `presets/<name>/animation.yaml`

**Saved preset format:**

```yaml
gesture: 2finger_pinch
fps: 25
frames:
  - [800, 800, 800, 800, 500, 300]   # [pinky, ring, middle, index, thumb_bend, thumb_rot]
  - [820, 820, 820, 830, 480, 320]
```

**Playback speed** — a play-speed slider linearly resamples the frame timeline (0.5×–2×).

**Serial auto-detection** — `scan_serial_ports()` enumerates USB-serial adapters by VID and
maps them to left/right hands; the mapping persists in `config/serial_map.json` across restarts.

## Why it exists (the architecture idea)

The recorded gesture library becomes the runtime gesture set: a perception layer (MediaPipe)
classifies the operator's hand state — pinch / grab / point — and the matching recorded
animation plays back on the robot. Precise, repeatable hand behaviors get authored **by
demonstration** instead of by hand-editing angle arrays.

Included presets: `2finger_pinch`, `3finger_pinch`, `grab`.

## How it was made

Started as a debugging tool (six sliders to poke the hand's registers) and grew into the
authoring pipeline: live streaming came first, then take recording, then the combine/sequence
system when single takes proved too limiting for multi-stage gestures. The two themes exist
because the GUI runs in different lab lighting setups.

## Key files

- `src/recorder.py` — the Flet GUI: sliders, takes, combine/sequence, preset save/load
- `presets/` — shipped gesture animations (YAML)
- `final_saves/` — kept recordings
- `config/serial_map.json` — persisted USB-port ↔ hand-side mapping
