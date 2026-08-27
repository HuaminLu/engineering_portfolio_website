# MuJoCo Arm Policy — RL Goal-Reaching Without an IK Solver

A PPO (Proximal Policy Optimization) agent trained in a MuJoCo simulation of the G1 solves
7-joint inverse kinematics in real time. A red sphere in the 3-D viewer is the Cartesian
end-effector goal; the policy drives the arm to it. WASDQE keys move the goal in space, and
pressing **R** mirrors the simulated joint angles to the physical robot over DDS.

## How to run

```bash
.venv/bin/python g1_arm_policy_controller.py
# opens the MuJoCo sim; streams to the robot only when you press r
# add --iface <g1-iface> if your G1 Ethernet port isn't the default
```

Requires the project `.venv` (stable_baselines3 + mujoco). The vendored `unitree_mujoco`
simulator folder was stripped from this upload — clone Unitree's `unitree_mujoco` alongside
if you need to retrain.

## Training setup

- **Algorithm:** PPO via Stable-Baselines3, `MlpPolicy`, 16 parallel envs (`DummyVecEnv`)
- **Trained for ~53 million timesteps** (`models/ppo_g1_left_53178k.zip`)
- **Physics:** MuJoCo model `g1_29dof_with_hand.xml`; waist and unused arm are locked every
  step so learning focuses entirely on the 7 left-arm joints

**Observation space (24-D):**

| Component | Dim | Meaning |
|---|---|---|
| q | 7 | joint angles (rad) |
| dq | 7 | joint velocities (rad/s) |
| p_hand | 3 | end-effector position (FK) |
| p_goal | 3 | goal position |
| Δp | 3 | goal − hand error vector |
| step_frac | 1 | episode progress ∈ [0, 1] |

**Action space (7-D):** `Δq ∈ [−0.05, +0.05]` rad/joint/step — incremental deltas, not
absolute angles, which bounds per-step motion and prevents jerks.

**Reward (dense):**

```python
reward = -5.0 * dist - 0.1 * norm(action)
# +1.0 when dist < 0.02 m (goal reached)
# -1.5 * (1 - vertical_alignment)   # keep the grasp axis vertical
# -1.5 * horiz_deviation            # palm-plane regularizer
# large negative + terminate on self-collision
```

Episode horizon scales with initial distance: `⌈‖Δp₀‖ / 0.05⌉ + 10` steps.
Joint limits come from the URDF (e.g. elbow `[−1.047, +2.094]` rad).

## Sim-to-real deployment

Inference loop at ~25 Hz: `predict → q += Δq → mj_forward → render`. On **R**, each frame's
joint targets are packed into a Unitree `LowCmd_` and published to `rt/arm_sdk`:

```python
for i, joint_idx in enumerate(ARM_JOINT_INDICES):
    msg.motor_cmd[joint_idx].q  = q_target[i]
    msg.motor_cmd[joint_idx].kp = 40.0   # position stiffness
    msg.motor_cmd[joint_idx].kd = 1.0    # damping
msg.motor_cmd[29].q = 1.0                # arm_sdk enable flag
# CRC-32 appended, then Write() over CycloneDDS
```

The robot's motor firmware runs the PD law `τ = kp·(q_cmd−q) + kd·(dq_cmd−dq) + tau_ff`.
`ChannelFactoryInitialize(0, iface)` binds DDS to the G1's wired interface (192.168.123.x).

## How it was made

The environment (`g1_arm_rl_env.py`) came first — getting the observation, bounded-delta
action space, and self-collision termination right took most of the iteration. The
orientation-shaping terms were added after early policies reached goals with the palm in
unusable orientations for grasping. Training ran in stages to ~53M steps; the model
checkpoint naming records the count.

## Key files

- `g1_arm_rl_env.py` — Gymnasium environment: observation/action spaces, reward, limits
- `train_g1_arm_policy.py` — PPO training entry (16 parallel envs)
- `g1_arm_policy_controller.py` — interactive runtime: MuJoCo viewer, WASDQE goal, R-to-robot
- `models/ppo_g1_left_53178k.zip` — the trained policy (53,178k steps)
