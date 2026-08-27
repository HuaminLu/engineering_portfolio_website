# WASDQE Walk — High-Level Bipedal Locomotion Teleop

A compact, always-on-top Flet GUI that walks the G1 with the keyboard. W/S = forward/back,
A/D = turn, Q/E = strafe, SHIFT = fast, SPACE = stop, with an ENABLE safety switch. The
operator never touches joint angles — this is the *high-level* control plane: you send a
body-velocity setpoint and the G1's onboard `ai_sport` controller owns footstep planning,
balance, and leg inverse kinematics.

## How to run

```bash
python3 keyboard_walk.py --iface enxa0cec8b8657b   # or omit --iface: it auto-detects
```

System python3 (flet 0.86). The G1's wired Ethernet port must be up on `192.168.123.x`.

## How it works

**15 Hz drive loop:**

```python
code = loco_client.SetVelocity(vx, vy, omega, duration=864000.0)
# vx forward/back (m/s), vy strafe (m/s), omega yaw rate (rad/s)
```

**Tap-to-inch motion model** — each keypress fires one bounded velocity pulse for
`PULSE_SECS = 0.45 s`, then auto-stops. Speed never accumulates; re-pressing re-fires the
same pulse. Precise, controllable positioning by design:

```python
SPEED_MIN = 0.15   # m/s minimum pulse
SPEED_MAX = 1.20   # m/s maximum pulse (SPEED slider scales; SHIFT ×1.6)
ANG_RATIO = 1.25   # rad/s of turn per 1 m/s linear
VMAX_LIN  = 1.4    # m/s hard safety clamp
VMAX_ANG  = 2.0    # rad/s hard safety clamp
```

**FSM / safety exits:**

```python
bot.SetFsmId(200)      # engage walking FSM state
bot.SetBalanceMode(1)  # continuous gait (vs. static hold)
bot.StopMove()         # SPACE — immediate stop
bot.Damp()             # Z — joints go compliant (fall gently)
bot.ZeroTorque()       # ESC — all motors off
```

## The bug that took the longest: error 3102

Every locomotion command failed with error 3102 (`RPC_ERR_CLIENT_SEND`) despite a healthy
network. Root cause: firmware `ai_sport ≥ 8.2.0.0` renamed the loco RPC service from
`"loco"` to `"sport"`, while the vendored SDK hardcodes `"loco"`. The fix patches the module
constant before the client is constructed:

```python
import unitree_sdk2py.g1.loco.g1_loco_api as _loco_api
_loco_api.LOCO_SERVICE_NAME = "sport"   # firmware ai_sport >= 8.2.0.0
```

Two more quality-of-life pieces:

- **NIC auto-detection** — `_resolve_g1_iface()` scans interfaces via the Linux `SIOCGIFADDR`
  ioctl and picks the one holding a `192.168.123.x` address.
- **Noise filter** — the vendored SDK's lease thread prints a harmless error line every second;
  a `_NoiseFilter` stdout wrapper intercepts those lines by pattern.

## How it was made

Started as a minimal `LocoClient` test script that "didn't work" — which turned into the 3102
service-name investigation above. Once commands landed, the GUI grew around operator safety:
the ENABLE gate, the pulse model (continuous velocity felt uncontrollable indoors), the hard
clamps, and the three-tier stop (StopMove / Damp / ZeroTorque).

## Key files

- `keyboard_walk.py` — everything: GUI, drive loop, pulse model, FSM management, the 3102 patch
- `README.md` — original project notes
