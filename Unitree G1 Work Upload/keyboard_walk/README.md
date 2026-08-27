# keyboard_walk

A compact Flet GUI to **walk the Unitree G1 with the keyboard**. It clones the
tele-op drive logic from sentdex's `run_geoff_gui.py` into a small always-on-top
window: press `W A S D Q E`, a 15 Hz loop ramps the velocity and streams
`LocoClient.Move(vx, vy, omega)` to the robot.

## Controls

```text
        Q   W   E          W / S : forward / back      (vx)
        A   S   D          A / D : rotate left / right (omega)
                           Q / E : strafe left / right (vy)

  SPACE = stop      SHIFT = fast      Z = damp + quit      ESC = stop + quit
```

- Keys ramp velocity up while held (step 0.05 m/s linear, 0.20 rad/s turn) and
  snap to 0 when released. Cap is 0.6 (1.2 while holding **Shift**).
- The on-screen key boxes are also **click-and-hold** with the mouse — handy
  because Flet reports key *presses* but not releases, so held keys are tracked
  with a short (0.35 s) timeout while the mouse boxes give precise press/release.
- **SPACE** forces an immediate stop at any time.

## Safety

- The drive loop sends **nothing** until you flip **ENABLE DRIVE** on. Turn it
  off (or press SPACE) to stop instantly.
- The robot must already be **standing / balance-ready** (use the official
  `g1_loco_client_example.py` or the remote to stand it up first). This tool only
  sends `Move` velocity commands.
- Clear the area around the robot before enabling drive.

## Run

Must run where `unitree_sdk2py` is importable **and** the chosen interface is on
the G1 network (`192.168.123.x`). The SDK vendored in the sibling
`arm_mimic/sentdex/unitree_sdk2_python` is auto-added to `sys.path`, so no
separate install is needed on this workstation.

```bash
cd 000-projects/active/keyboard_walk

# real robot — pick the interface that's on the G1 network
python3 keyboard_walk.py --iface enxa0cec8b8657b

# UI only, no robot / no DDS (test the layout + key highlighting)
python3 keyboard_walk.py --dry-run
```

| Flag | Meaning |
|------|---------|
| `--iface` | network interface on the G1 network (default `enxa0cec8b8657b`) |
| `--dry-run` | UI only — no DDS / no LocoClient |

## How it maps to the SDK

Canonical bring-up (from `g1_loco_client_example.py`):

```python
ChannelFactoryInitialize(0, iface)
bot = LocoClient(); bot.SetTimeout(10.0); bot.Init()
bot.Move(vx, vy, omega)     # vx fwd/back, vy strafe, omega rotate
bot.StopMove(); bot.Damp(); bot.ZeroTorque()
```

The loop mirrors `run_geoff_gui._on_drive_tick`: step + clamp each axis from the
held keys, `Move(..., continous_move=True)` every tick, and toggle
`SetBalanceMode(0/1)` (static hold when idle, continuous gait when moving) to
avoid the "walking in place" quirk.

## Layout

| Path | What |
|------|------|
| `keyboard_walk.py` | the whole app: `LocoBackend` (SDK) + `KeyboardWalkApp` (Flet UI) |
