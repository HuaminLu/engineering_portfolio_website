# Unitree G1 Work Upload

Five working control projects for the **Unitree G1 EDU 29-DOF humanoid robot**, built by Huamin (Lucas) Lu.
Each folder is self-contained and has an `AGENTS.md` explaining how it works, how it was built, and how to run it.

| Folder | Project | Control plane |
|---|---|---|
| `hand_mimic/` | Real-time camera → robot hand teleoperation | RS-485 serial (Inspire hand registers) |
| `hand_preset_mimic/` | Hand Recorder GUI — keyframe gesture animator | RS-485 serial |
| `arm_policy_rl/` | PPO reinforcement-learning arm reach policy (MuJoCo → real robot) | DDS `rt/arm_sdk` @ 25 Hz |
| `arm_recorder/` | 14-DOF arm trajectory recorder / animator GUI | DDS `rt/arm_sdk` @ 50 Hz |
| `keyboard_walk/` | WASDQE bipedal walking teleop | High-level LocoClient RPC @ 15 Hz |

## Platform

- Unitree G1 EDU — 1.3 m, ~35 kg, 29 actuated joints (7 × 2 arms, 6 × 2 legs, 3 waist)
- Inspire RH56 5-finger dexterous hands (6 DOF each, RS-485 @ 115200 baud)
- Jetson Orin NX onboard · Livox Mid-360 LiDAR · Intel RealSense D435
- CycloneDDS middleware over wired Ethernet (`192.168.123.x` subnet)

## The three control planes these projects cover

1. **Low-level joint streaming** (`arm_policy_rl`, `arm_recorder`) — publish `q, dq, tau, kp, kd`
   for every joint at 50 Hz on `rt/arm_sdk`; your code owns the motion. The motor firmware applies
   `τ = kp·(q_cmd − q) + kd·(dq_cmd − dq) + tau_ff`.
2. **High-level locomotion RPC** (`keyboard_walk`) — send a `vx, vy, ω` body-velocity setpoint;
   the G1's onboard controller owns gait, balance, and leg IK.
3. **Serial register control** (`hand_mimic`, `hand_preset_mimic`) — write 6-DOF angle integers
   over RS-485; the Inspire hand firmware owns the servo loops.

> Vendored third-party SDKs (librealsense, Livox SDK, unitree_sdk2_python, unitree_mujoco),
> virtualenvs, logs, and recovery snapshots were stripped from this upload — each AGENTS.md
> lists what to reinstall to run the code.
