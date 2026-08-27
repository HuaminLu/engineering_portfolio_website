# hand_preset_mimic — quickstart

Gesture-triggered preset playback for the Inspire RH56 dexterous hands.
Replaces real-time retargeting with 3 recorded gestures for reliability.

**Gestures detected:**
| Gesture | Trigger | Preset folder |
|---------|---------|---------------|
| 2-finger pinch | thumb + index close | `presets/2finger_pinch/` |
| 3-finger pinch | thumb + index + middle close | `presets/3finger_pinch/` |
| Grab | all 4 fingertips curled to palm | `presets/grab/` |
| (none) | hand open / no hand | open position (all zeros) |

## Run

```bash
bash run.sh
# or with dry-run (no hardware):
bash run.sh --dry-run
```

## Preset format

Each gesture folder contains `preset.yaml`:
```yaml
# angles: [pinky, ring, middle, index, thumb_bend, thumb_rot]
# 0 = open, 1000 = closed
angles: [800, 800, 800, 800, 500, 300]
```

Edit these files to tune the positions, or use the animation recorder tool
(to be built) to record smooth animation sequences.

## Layout

| Path | What |
|------|------|
| `src/mimic.py` | main loop: perception + gesture -> preset dispatch |
| `src/_config.py` | YAML calibration loader (from hand_mimic) |
| `src/_source.py` | frame source abstraction (from hand_mimic) |
| `config/hand_preset_mimic.yaml` | pinch threshold + camera defaults |
| `presets/<gesture>/preset.yaml` | static endpoint per gesture |
| `run.sh` | launch script |

## Dependencies

- `dexkit` at `../../300-software/60-libs/dexkit/` (driver + perception)
- MediaPipe model at `config/hand_landmarker.task`
- uv venv in project root (or workstation venv from hand_mimic)
