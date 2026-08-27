#!/usr/bin/env python3.10
"""g1_arm_policy_controller.py – interactive reach controller driven by a
pre-trained PPO policy.

The script lets you *move the Cartesian goal* of the G-1 arm with simple
W/A/S/D/Q/E keys while a frozen RL policy (trained via
``train_g1_arm_policy.py``) takes care of all joint-level motions necessary
to reach that target.

Key bindings
============
  w  : goal ↑  (+z)
  s  : goal ↓  (−z)
  a  : goal ←  (+y for left arm)
  d  : goal →  (−y)
  q  : goal forward  (+x)
  e  : goal backward (−x)

Additional controls
  r  : toggle commands to **R**obot (Unitree SDK-2)
  s  : toggle commands to **S**imulation viewer
  Esc/q : quit

Two separate outputs are supported:
  • MuJoCo simulation (always available, "sim")
  • The real robot via SDK-2 ("robot") – optional, requires ``unitree_sdk2py``

By default only *simulation* is enabled.  Use the *r* key to also stream the
current joint targets to the physical robot.

The policy file as well as the arm side can be selected via command-line
flags – see ``--help`` for details.
"""

from __future__ import annotations

import argparse
import math
import pathlib
import threading
import time
from types import SimpleNamespace
from typing import Dict, List, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# 0.  Stable-Baselines3 policy loader
# ---------------------------------------------------------------------------


def load_policy(path: pathlib.Path):  # noqa: D401
    from stable_baselines3 import PPO  # type: ignore

    print(f"[policy] Loading PPO model from {path} …")
    policy = PPO.load(str(path), device="cpu")
    policy.set_parameters(policy.get_parameters())  # ensure deterministic
    policy.policy.set_training_mode(False)
    return policy


# ---------------------------------------------------------------------------
# 1.  Interactive MuJoCo environment
# ---------------------------------------------------------------------------


def make_env(render: bool, right_arm: bool):  # noqa: D401
    import g1_arm_rl_env as _env

    # Always use "none" — run_viewer creates the single passive viewer itself.
    # Passing "human" causes the env.reset() to open a second GLFW window which
    # segfaults when our viewer tries to open a third GL context.
    return _env.G1ArmReachEnv(render_mode="none", right_arm=right_arm)


# ---------------------------------------------------------------------------
# 1b.  Auto-detect the G1 wired-bus interface (192.168.123.x)
# ---------------------------------------------------------------------------


def _iface_ipv4(name: str):
    import fcntl
    import socket
    import struct
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        packed = struct.pack("256s", name[:15].encode())
        return socket.inet_ntoa(fcntl.ioctl(s.fileno(), 0x8915, packed)[20:24])
    except OSError:
        return None
    finally:
        s.close()


def resolve_g1_iface(preferred: str | None = None) -> str:
    """Return the NIC currently holding a 192.168.123.x address (the G1 wired
    bus) so DDS binds correctly no matter which dongle / name is in use.
    Prefers ``preferred`` if it's on the bus, else auto-picks a wired NIC."""
    import os
    try:
        names = [n for n in os.listdir("/sys/class/net") if n != "lo"]
    except OSError:
        names = []

    def on_bus(n):
        ip = _iface_ipv4(n)
        return bool(ip and ip.startswith("192.168.123."))

    if preferred and preferred in names and on_bus(preferred):
        return preferred
    wired = [n for n in names if n.startswith(("en", "eth"))]
    for n in wired + [n for n in names if n not in wired]:
        if on_bus(n):
            return n
    return preferred or ""


# ---------------------------------------------------------------------------
# 2.  Robot bridge (copied from g1_arm_sim_controller)
# ---------------------------------------------------------------------------


class RobotBridge:
    """Very small wrapper to publish ``LowCmd`` messages every cycle."""

    def __init__(self, iface: str, domain: int):
        try:
            from unitree_sdk2py.core.channel import (  # type: ignore
                ChannelFactoryInitialize,
                ChannelPublisher,
            )
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_  # type: ignore
            from unitree_sdk2py.idl.default import (  # type: ignore
                unitree_hg_msg_dds__LowCmd_,
            )
        except Exception:
            print("[robot] SDK-2 not present – robot output disabled")
            self.ok = False
            return

        try:
            ChannelFactoryInitialize(domain, iface)
            self._pub = ChannelPublisher("rt/arm_sdk", LowCmd_)
            self._pub.Init()

            self._cmd = unitree_hg_msg_dds__LowCmd_()

            # arm_sdk owns the whole upper body while enabled. Command:
            #   - arms (15-28): kp=40 kd=1  (driven by the policy)
            #   - torso/waist (12,13,14): LOCKED upright at q=0 with firm stiffness
            #       (leaving them un-commanded makes them go damp -> torso droops)
            #   - legs (0-11): kp=kd=0  (left to the robot's balance controller)
            KP_WAIST, KD_WAIST = 200.0, 5.0
            for i, mc in enumerate(self._cmd.motor_cmd):
                mc.mode = 0
                if 15 <= i <= 28:
                    mc.kp, mc.kd = 40.0, 1.0
                elif i in (12, 13, 14):
                    mc.q, mc.dq, mc.tau = 0.0, 0.0, 0.0
                    mc.kp, mc.kd = KP_WAIST, KD_WAIST
                else:
                    mc.kp, mc.kd = 0.0, 0.0

            if 29 < len(self._cmd.motor_cmd):
                self._cmd.motor_cmd[29].q = 1.0

            try:
                from unitree_sdk2py.utils.crc import CRC  # type: ignore

                self._crc = CRC()
            except Exception:
                self._crc = None

            self.ok = True
        except Exception as e:
            print(f"[robot] DDS initialisation failed – robot disabled ({e})")
            self.ok = False

    # ------------------------------
    def send_qpos(self, q: Dict[int, float]) -> None:  # noqa: D401 – 29-DoF idx→rad
        if not self.ok:
            return

        # Only the first 29 entries are motors – skip hands for safety
        for idx, val in q.items():
            if idx >= 29:
                continue
            if idx < len(self._cmd.motor_cmd):
                self._cmd.motor_cmd[idx].q = float(val)

        # Recent versions of ``unitree_sdk2py`` renamed the public CRC helper
        # from ``calculate_crc`` to ``Crc`` (uppercase "C").  To stay compatible
        # with both variants we look for either attribute at runtime.

        if self._crc is not None:
            # Prefer the newer ``Crc`` method if available, otherwise fall back
            # to the old name so older SDK-2 checkouts continue to work.
            if hasattr(self._crc, "Crc"):
                self._cmd.crc = self._crc.Crc(self._cmd)
            elif hasattr(self._crc, "calculate_crc"):
                self._cmd.crc = self._crc.calculate_crc(self._cmd)

        self._pub.Write(self._cmd)


# ---------------------------------------------------------------------------
# 3.  Joint index mapping (identical to g1_arm_sim_controller)
# ---------------------------------------------------------------------------


# Motor index, human-readable label, MuJoCo joint/actuator *prefix* (same as XML)
JOINTS: List[Tuple[int, str, str]] = [
    (15, "L shoulder-pitch", "left_shoulder_pitch"),
    (16, "L shoulder-roll",  "left_shoulder_roll"),
    (17, "L shoulder-yaw",   "left_shoulder_yaw"),
    (18, "L elbow",          "left_elbow"),
    (19, "L wrist-roll",     "left_wrist_roll"),
    (20, "L wrist-pitch",    "left_wrist_pitch"),
    (21, "L wrist-yaw",      "left_wrist_yaw"),
    (22, "R shoulder-pitch", "right_shoulder_pitch"),
    (23, "R shoulder-roll",  "right_shoulder_roll"),
    (24, "R shoulder-yaw",   "right_shoulder_yaw"),
    (25, "R elbow",          "right_elbow"),
    (26, "R wrist-roll",     "right_wrist_roll"),
    (27, "R wrist-pitch",    "right_wrist_pitch"),
    (28, "R wrist-yaw",      "right_wrist_yaw"),
]

# Convenience from idx → label
IDX2LABEL = {idx: lbl for idx, lbl, _ in JOINTS}


# ---------------------------------------------------------------------------
# 4.  Main control loop (MuJoCo passive viewer, no curses)
# ---------------------------------------------------------------------------


def run_viewer(env, policy, robot: RobotBridge, out_robot_init: bool, speed):  # noqa: D401
    """Run the RL policy loop using MuJoCo's passive viewer for display and input."""
    import mujoco
    import mujoco.viewer as mjv
    import sys
    import tty
    import termios
    import select

    STEP = 0.02

    obs, _ = env.reset()

    state = SimpleNamespace(
        p_goal=env.p_goal.copy(),
        out_robot=out_robot_init,
        hold_mode=False,
        collision_freeze=False,
        last_robot_send=0.0,
        last_safe_qpos=None,
        quit=False,
    )

    def handle_key(ch):
        k = state
        if ch in ('x', '\x1b'):
            k.quit = True
        elif ch == 'w':
            k.p_goal[2] += STEP
        elif ch == 's':
            k.p_goal[2] -= STEP
        elif ch == 'a':
            k.p_goal[1] += STEP
        elif ch == 'd':
            k.p_goal[1] -= STEP
        elif ch == 'q':
            k.p_goal[0] += STEP
        elif ch == 'e':
            k.p_goal[0] -= STEP
        elif ch == 'r':
            k.out_robot = not k.out_robot and robot.ok
            sys.stdout.write(f"\r[ctrl] Robot streaming: {'ON' if k.out_robot else 'OFF'}   \n")
            sys.stdout.flush()
        elif ch == ']':
            speed.dt = max(0.005, speed.dt * 0.5)
        elif ch == '[':
            speed.dt = min(0.5, speed.dt * 2.0)
        elif ch == '.':
            speed.mult = min(4.0, speed.mult * 2.0)
        elif ch == ',':
            speed.mult = max(0.25, speed.mult * 0.5)

    # Put terminal in raw mode so keypresses arrive immediately without Enter
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    tty.setraw(fd)

    try:
        sys.stdout.write("Viewer open — keep terminal focused. W/A/S/D/Q/E=goal  R=robot  [/]=speed  x=quit\r\n")
        sys.stdout.write(f"Robot: {'READY (r to enable)' if robot.ok else 'no SDK'}\r\n")
        sys.stdout.flush()

        with mjv.launch_passive(env.model, env.data) as viewer:
            while viewer.is_running() and not state.quit:
                t0 = time.time()

                # Read any pending keypresses (non-blocking)
                if select.select([sys.stdin], [], [], 0)[0]:
                    ch = sys.stdin.read(1)
                    handle_key(ch)

                # Clamp and apply goal
                state.p_goal = np.clip(state.p_goal, [-0.1, -0.6, 0.4], [0.6, 0.6, 1.4])
                env.p_goal[:] = state.p_goal
                if env._goal_mid != -1:
                    env.data.mocap_pos[env._goal_mid] = state.p_goal

                # Self-collision check
                collided = False
                if hasattr(env, "_arm_gids") and hasattr(env, "_protect_gids"):
                    arm_gids = env._arm_gids
                    prot_gids = env._protect_gids
                    mj = env._mujoco if hasattr(env, "_mujoco") else None
                    for i in range(env.data.ncon):
                        c = env.data.contact[i]
                        if mj is not None:
                            b1 = mj.mj_id2name(env.model, mj.mjtObj.mjOBJ_BODY, int(env.model.geom_bodyid[c.geom1]))
                            b2 = mj.mj_id2name(env.model, mj.mjtObj.mjOBJ_BODY, int(env.model.geom_bodyid[c.geom2]))
                            if (b1 and "hand" in b1) or (b2 and "hand" in b2):
                                continue
                        if (c.geom1 in arm_gids and c.geom2 in prot_gids) or (
                            c.geom2 in arm_gids and c.geom1 in prot_gids
                        ):
                            if max(0.0, -c.dist) >= 0.002:
                                collided = True
                                break

                if collided:
                    state.collision_freeze = True

                dist = np.linalg.norm(env.p_goal - env._fk())
                if state.collision_freeze:
                    state.hold_mode = True
                else:
                    if not state.hold_mode and dist < 0.03:
                        state.hold_mode = True
                    elif state.hold_mode and dist > 0.05:
                        state.hold_mode = False
                        state.collision_freeze = False

                if state.hold_mode:
                    obs, _, _, _, _ = env.step(np.zeros(env.action_space.shape, dtype=np.float32))
                    env._step_count = 0
                else:
                    action, _ = policy.predict(obs, deterministic=True)
                    action = np.clip(action * speed.mult, env.action_space.low, env.action_space.high)
                    obs, _, done, _, _ = env.step(action)
                    if not collided:
                        state.last_safe_qpos = env.data.qpos.copy()
                    if done:
                        obs, _ = env.reset()
                        if state.last_safe_qpos is not None:
                            env.data.qpos[:] = state.last_safe_qpos
                            env.data.qvel[:] = 0.0
                            env._mujoco.mj_forward(env.model, env.data)
                        env.p_goal[:] = state.p_goal
                        if env._goal_mid != -1:
                            env.data.mocap_pos[env._goal_mid] = state.p_goal

                # Robot streaming
                if state.out_robot and robot.ok and (time.time() - state.last_robot_send) > 0.02:
                    if not hasattr(env, "_motor_qadr"):
                        qadr = {}
                        for idx, _lbl, mj_short in JOINTS:
                            jname_joint = mj_short + "_joint"
                            jid = mujoco.mj_name2id(env.model, mujoco.mjtObj.mjOBJ_JOINT, jname_joint)
                            if jid != -1:
                                qadr[idx] = int(env.model.jnt_qposadr[jid])
                        env._motor_qadr = qadr
                    # Stream only the arm joints (15-28). The 3 torso motors are
                    # already held upright by the persistent stiffness set in
                    # RobotBridge.__init__ (q=0, kp=KP_WAIST), so we don't touch them.
                    qpos = {idx: float(env.data.qpos[adr]) for idx, adr in env._motor_qadr.items()
                            if idx >= 15}
                    robot.send_qpos(qpos)
                    state.last_robot_send = time.time()

                viewer.sync()
                elapsed = time.time() - t0
                if elapsed < speed.dt:
                    time.sleep(speed.dt - elapsed)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


# ---------------------------------------------------------------------------
# 6.  Entry-point
# ---------------------------------------------------------------------------


def main() -> None:  # noqa: D401
    ap = argparse.ArgumentParser(description="Interactive RL reach controller for Unitree G-1 arm")
    ap.add_argument("--model", default="models/ppo_g1_left_53178k.zip", help="Path to trained .zip model")
    ap.add_argument("--right-arm", action="store_true", help="Use policy for the RIGHT arm instead of left")
    ap.add_argument("--iface", default=None,
                    help="DDS interface (default: auto-detect the 192.168.123.x NIC)")
    ap.add_argument("--domain", type=int, default=0, help="DDS domain ID")
    ap.add_argument("--sim-only", action="store_true", help="Disable robot output entirely")
    # 40 ms matches the default control cycle on the real Unitree arm while
    # keeping the MuJoCo viewer responsive.
    ap.add_argument("--rate", type=float, default=0.04, help="Render / control loop interval (s)")

    args = ap.parse_args()

    model_path = pathlib.Path(args.model).expanduser()
    if not model_path.exists():
        raise SystemExit(f"Model file not found: {model_path}")

    policy = load_policy(model_path)

    env = make_env(render=False, right_arm=args.right_arm)

# Keep environment's own collision penalties disabled to avoid console spam
# – we implement our own pared-down collision check below.

    # ------------------------------------------------------------------
    # 1.  Apply recorded *real-robot* joint pose so the simulation starts in
    #     the exact same configuration as the hardware.  Then place the goal
    #     marker at the current wrist position so the episode begins with
    #     zero error (the arm does not have to move until the user jogs the
    #     target).
    # ------------------------------------------------------------------

    # Default "lego-figure" start pose: elbow bent ~90° so the active hand sits
    # in front of the torso, and the red goal dot is placed exactly at that hand
    # position — so every launch begins from the same natural, zero-error pose.
    try:
        import mujoco as _mj

        # [shoulder_pitch, shoulder_roll, shoulder_yaw, elbow, wrist_roll,
        #  wrist_pitch, wrist_yaw] — elbow 1.5708 rad = 90°.
        lego_pose = [0.30, 0.15, 0.0, 1.5708, 0.0, 0.0, 0.0]
        if args.right_arm:
            lego_pose[1] = -lego_pose[1]   # mirror shoulder-roll for the right arm
        for adr, q in zip(env._qadr, lego_pose):
            env.data.qpos[adr] = float(q)

        _mj.mj_forward(env.model, env.data)

        # Place the goal marker at the current hand position (zero initial error).
        if hasattr(env, "_fk"):
            p_hand = env._fk()
            env.p_goal[:] = p_hand
            if env._goal_mid != -1:
                env.data.mocap_pos[env._goal_mid] = env.p_goal
    except Exception:
        # MuJoCo import failed – fall back to the env's default reset pose.
        pass

    iface = args.iface if args.iface else resolve_g1_iface()
    if not args.sim_only:
        print(f"[policy] using DDS interface: {iface or '(none found on 192.168.123.x)'}")
    robot = RobotBridge(iface, args.domain) if not args.sim_only else RobotBridge("", 0)

    # Start at quarter-speed so new users have more time to react; can be
    # increased on-the-fly via the "," / "." keys.
    speed = SimpleNamespace(dt=max(0.005, args.rate), mult=0.25)

    run_viewer(env, policy, robot, out_robot_init=False, speed=speed)


if __name__ == "__main__":
    main()
