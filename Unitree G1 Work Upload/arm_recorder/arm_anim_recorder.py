#!/usr/bin/env python3
"""arm_anim_recorder.py — slider-based arm trajectory recorder for the G1 (Flet).

A from-scratch port of the Inspire-hands Animation Recorder
(``hand_preset_mimic/src/recorder.py``) to the Unitree G1 arms over ``rt/arm_sdk``.

Instead of being told a direction and physically shoving the arm (the old
``arm_train_recorder.py`` flow — tedious, and you nudge joints you didn't mean
to), you dial each of the 7 arm joints with its own slider.  The arm tracks the
sliders live over DDS while the torso is locked upright (3 waist motors held at
0).  Craft a movement from a fixed home pose, record the full joint
trajectory (every joint, every frame), merge single-joint takes into coordinated
motions, play forward/reverse, then save it as a trajectory sample.

Workflow:
  1. Default Position  → both arms snap to the home pose (elbow 90°, rest 0)
  2. Pick ARM + DIRECTION (e.g. LEFT · up)
  3. REC → move the joints you want → STOP        (creates a Take)
  4. (optional) check 2+ takes → COMBINE          (parallel merge)
  5. Save Sample  → appends the crafted path to
                    data/trajectories/<arm>/<direction>.yaml
  6. Repeat for slightly-different samples, then:
        python3 data/build_arm_traj.py --arm left
     and play them back with arm_gui.py.

Usage:
    python3 arm_anim_recorder.py --iface enxa0cec8b8657b     # real robot
    python3 arm_anim_recorder.py --iface lo --dry-run        # UI only, no DDS
    python3 arm_anim_recorder.py --web                        # browser view

Only ONE rt/arm_sdk publisher at a time — do not run alongside arm_gui.py or
arm_train_recorder.py.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import flet as ft
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent
TRAJ_DIR     = PROJECT_ROOT / "data" / "trajectories"
PRESETS_DIR  = PROJECT_ROOT / "data" / "presets"
FINAL_DIR    = PROJECT_ROOT / "data" / "final_saves"
HOME_FILE    = PROJECT_ROOT / "config" / "arm_home.json"


class _FlowList(list):
    """A list rendered inline (flow style) by yaml.dump, so each frame's joint
    vector stays on one readable line instead of exploding to one float/line."""


def _flow_list_representer(dumper, data):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


yaml.add_representer(_FlowList, _flow_list_representer)

# --------------------------------------------------------------------------- #
#  Joint layout (Unitree G-1, unitree_hg LowCmd motor indices)
# --------------------------------------------------------------------------- #
# NOTE: the 3 torso/waist motors (12,13,14) are locked upright at q=0 with firm
# stiffness in _tick_once (arm_sdk damps them otherwise). The 14 arm joints below
# are the ones actually animated.
LEFT_IDX  = list(range(15, 22))   # 15..21
RIGHT_IDX = list(range(22, 29))   # 22..28
NOT_USED_IDX = 29                 # motor_cmd[29].q = 1 enables arm_sdk control
NJ = 7                            # joints per arm

# Human-readable labels, shoulder → wrist, matched to g1_29dof.xml joint order.
JOINT_LABELS = [
    "Shoulder Pitch",     # 15/22  raise arm fwd/back
    "Shoulder Roll",      # 16/23  arm out/in
    "Upper Arm Rotate",   # 17/24  shoulder yaw
    "Elbow",              # 18/25
    "Lower Arm Rotate",   # 19/26  wrist roll (forearm)
    "Wrist Up/Down",      # 20/27  wrist pitch
    "Wrist Left/Right",   # 21/28  wrist yaw
]

# Physical meaning of each slider's min (left) and max (right) end.
JOINT_ENDPOINTS = [
    ("Back",    "Fwd"),      # Shoulder Pitch  — arm sweeps back / raises forward
    ("In",      "Out"),      # Shoulder Roll   — adduct toward body / abduct outward
    ("↺ Rot",   "Rot ↻"),   # Upper Arm Rotate — CCW / CW viewed from above
    ("Extend",  "Flex"),     # Elbow           — straight / bent
    ("Supin.",  "Pronat."),  # Lower Arm Rotate — palm up / palm down
    ("Down",    "Up"),       # Wrist Up/Down   — wrist down / wrist up
    ("Left",    "Right"),    # Wrist Left/Right — wrist left / wrist right
]

# Per-joint (min, max) in radians, from the URDF. Only shoulder-roll differs L/R.
LIMITS_LEFT = [
    (-3.089, 2.670), (-1.588, 2.252), (-2.618, 2.618), (-1.047, 2.094),
    (-1.972, 1.972), (-1.614, 1.614), (-1.614, 1.614),
]
LIMITS_RIGHT = [
    (-3.089, 2.670), (-2.252, 1.588), (-2.618, 2.618), (-1.047, 2.094),
    (-1.972, 1.972), (-1.614, 1.614), (-1.614, 1.614),
]

def _limits(side: str):
    return LIMITS_LEFT if side == "left" else LIMITS_RIGHT

# Home / default pose: every joint at 0 (neutral) so all sliders start centered.
HOME_POSE = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

# Joints that flip sign when mirroring left <-> right (roll / yaw axes).
MIRROR_FLIP = {1, 2, 4, 6}   # shoulder_roll, shoulder_yaw, wrist_roll, wrist_yaw

DIRECTIONS = ["up", "down", "left", "right", "forward", "back"]

# Default arm-motion presets shown in the PRESET card (permanent = can't remove)
GESTURES  = ["up", "down", "left", "right", "forward", "back"]
PERMANENT = set(GESTURES)

REC_HZ  = 25
REC_DT  = 1.0 / REC_HZ
SEND_HZ = 50
SEND_DT = 1.0 / SEND_HZ

KP_STIFF, KD_STIFF = 60.0, 1.5
# Torso/waist lock gains. Enabling arm_sdk hands the 3 waist motors (12,13,14)
# to the SDK; if we don't command them they go fully damp and the torso droops.
# We hold them at q=0 with firm stiffness so the robot stands upright. Tunable:
# raise if the torso still sags, lower if it jolts/oscillates on connect.
WAIST_IDX = [12, 13, 14]    # waist yaw, roll, pitch
KP_WAIST, KD_WAIST = 200.0, 5.0
MOVE_THRESHOLD = 0.02       # rad — min joint travel for a take to count as "moving"
SEGMENT_SETTLE_SEC = 0.5    # hold at each take boundary during playback
PLAY_TOL = 0.02             # rad — a frame counts as "reached" within this of cmd
PLAY_FRAME_TIMEOUT = 200    # ticks (~4 s @ 50 Hz) before force-advancing a stuck frame
FOLLOW_SCALE = 12.0         # ramp-step boost during playback so the arm tracks the
                            # wall-clock frames tightly (speed comes from the timeline,
                            # NOT the step — like the hands recorder's immediate sends)


def _clamp(side: str, idx: int, v: float) -> float:
    lo, hi = _limits(side)[idx]
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
#  Network interface discovery (for the ethernet/wifi connect dropdown)
# --------------------------------------------------------------------------- #
# Subnets that mean "on the same network as the G1". The internal wired bus is
# 192.168.123.0/24 (PC2 = .164, PC1 = .161); the on-robot WiFi AP hands out
# 192.168.124.x / 10.42.0.x. Being on any of these = reachable.
G1_NETS = ("192.168.123.", "192.168.124.", "10.42.0.")


def _iface_ipv4(name: str) -> str | None:
    """IPv4 address of an interface (Linux SIOCGIFADDR), or None."""
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


def _list_net_ifaces() -> list[dict]:
    """Enumerate real ethernet + wifi interfaces with IP / link / on-G1 status.

    Returns dicts: {name, kind: 'eth'|'wifi', ip, up: bool, on_g1: bool}.
    Skips loopback and virtual/bridge/container interfaces.
    """
    import os
    base = "/sys/class/net"
    out: list[dict] = []
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return out
    for n in names:
        if n == "lo" or n.startswith(("docker", "veth", "br-", "virbr", "tap", "tun")):
            continue
        is_wifi = os.path.exists(f"{base}/{n}/wireless") or n.startswith(("wl", "wlan"))
        try:
            oper = open(f"{base}/{n}/operstate").read().strip()
        except OSError:
            oper = "unknown"
        ip = _iface_ipv4(n)
        out.append({
            "name": n,
            "kind": "wifi" if is_wifi else "eth",
            "ip": ip,
            "up": oper == "up",
            "on_g1": bool(ip and any(ip.startswith(p) for p in G1_NETS)),
        })
    return out


def _resolve_g1_iface(preferred: str | None = None) -> str:
    """Return the NIC currently on the G1 wired bus (192.168.123.x) so the tool
    binds DDS correctly regardless of which dongle / interface name is in use.
    Prefers ``preferred`` if it's already on the bus; else auto-picks a wired
    NIC with a 192.168.123.x address; else falls back to ``preferred``."""
    ifaces = _list_net_ifaces()
    names = [nf["name"] for nf in ifaces]
    def _bus(name):
        nf = next((x for x in ifaces if x["name"] == name), None)
        return bool(nf and nf["ip"] and nf["ip"].startswith("192.168.123."))
    if preferred and preferred in names and _bus(preferred):
        return preferred
    wired = [nf["name"] for nf in ifaces if nf["kind"] == "eth"
             and nf["ip"] and nf["ip"].startswith("192.168.123.")]
    if wired:
        return wired[0]
    return preferred or ""


# --------------------------------------------------------------------------- #
#  Themes (ported from the hands recorder)
# --------------------------------------------------------------------------- #
THEMES = {
    "yellow": {
        "BG": "#0f0f0f", "CARD": "#1a1a1a", "CARD2": "#212121", "ITEM": "#2a2a2a",
        "BORDER": "#3a3a00", "FG": "#f5f0d0", "FG_DIM": "#a09060", "FG_VAL": "#ffe033",
        "LBL": "#e8d89a", "SUBTITLE": "#a09060",
        "OK": "#a3e635", "ERR": "#f87171", "ACCENT": "#f5c800", "TEAL": "#fde047",
        "INDIGO": "#d4a017", "PURPLE": "#c08000", "NEUTRAL": "#3a3a3a", "DANGER": "#ef4444",
        "SLIDER_ACTIVE": "#d4a000", "SLIDER_THUMB": "#f5c800",
        "SPEED_ACTIVE": "#a07800", "SPEED_THUMB": "#d4a000",
        "EMPTY_BG": "#181800", "EMPTY_BORDER": "#3a3a00",
        "EMPTY_ICON": "#806000", "EMPTY_TEXT": "#a09040",
    },
    "blue": {
        "BG": "#06080f", "CARD": "#0d1f3c", "CARD2": "#132848", "ITEM": "#1a3458",
        "BORDER": "#2a4a7f", "FG": "#e8f0fd", "FG_DIM": "#7a9fcf", "FG_VAL": "#93c5fd",
        "LBL": "#e8f0fd", "SUBTITLE": "#7a9fcf",
        "OK": "#34d399", "ERR": "#f87171", "ACCENT": "#3b82f6", "TEAL": "#38bdf8",
        "INDIGO": "#818cf8", "PURPLE": "#c084fc", "NEUTRAL": "#64748b", "DANGER": "#ef4444",
        "SLIDER_ACTIVE": "#1d4ed8", "SLIDER_THUMB": "#1e40af",
        "SPEED_ACTIVE": "#6d28d9", "SPEED_THUMB": "#5b21b6",
        "EMPTY_BG": "#0f2547", "EMPTY_BORDER": "#1a4a8a",
        "EMPTY_ICON": "#4a7ec7", "EMPTY_TEXT": "#6ea4dd",
    },
    "bw": {
        "BG": "#000000", "CARD": "#2b2b2b", "CARD2": "#333333", "ITEM": "#3d3d3d",
        "BORDER": "#4a4a4a", "FG": "#ffffff", "FG_DIM": "#8a8a8a", "FG_VAL": "#ffffff",
        "LBL": "#9a9a9a", "SUBTITLE": "#8a8a8a",
        "OK": "#34d399", "ERR": "#f87171", "ACCENT": "#ffffff", "TEAL": "#e0e0e0",
        "INDIGO": "#cccccc", "PURPLE": "#d9d9d9", "NEUTRAL": "#5a5a5a", "DANGER": "#c0392b",
        "SLIDER_ACTIVE": "#ffffff", "SLIDER_THUMB": "#ffffff",
        "SPEED_ACTIVE": "#e0e0e0", "SPEED_THUMB": "#ffffff",
        "EMPTY_BG": "#262626", "EMPTY_BORDER": "#4a4a4a",
        "EMPTY_ICON": "#9a9a9a", "EMPTY_TEXT": "#c8c8c8",
    },
}

BG = CARD = CARD2 = ITEM = BORDER = FG = FG_DIM = FG_VAL = ""
LBL = SUBTITLE = ""
OK = ERR = ACCENT = TEAL = INDIGO = PURPLE = NEUTRAL = DANGER = GREEN = ""
SLIDER_ACTIVE = SLIDER_THUMB = SPEED_ACTIVE = SPEED_THUMB = ""
EMPTY_BG = EMPTY_BORDER = EMPTY_ICON = EMPTY_TEXT = ""


def _apply_theme(name: str):
    pal = THEMES.get(name, THEMES["yellow"])
    g = globals()
    for k, v in pal.items():
        g[k] = v
    g["GREEN"] = pal["TEAL"]


_apply_theme("yellow")


# --------------------------------------------------------------------------- #
#  Arm channel — one DDS publisher for BOTH arms (rt/arm_sdk)
# --------------------------------------------------------------------------- #
class ArmChannel:
    """Owns the rt/arm_sdk publisher + a 50 Hz ramp loop for both arms.

    ``slider_vals`` (per side, 7 floats, radians) is the source of truth set by
    the UI; the publish loop ramps the commanded joints toward it and writes the
    LowCmd.  The 3 torso/waist motors are locked upright at 0 with firm stiffness
    (arm_sdk would damp them otherwise); only the 14 arm joints are animated.
    Recording reads ``slider_vals`` directly.
    """

    def __init__(self, iface: str, step: float = 0.02, dry_run: bool = False):
        self.iface = iface
        self.step = step
        self.dry_run = dry_run

        self._lock = threading.Lock()
        self.slider_vals = {"left": list(HOME_POSE), "right": list(HOME_POSE)}
        self.cmd_q = {i: 0.0 for i in (*LEFT_IDX, *RIGHT_IDX)}
        self.joint_cur: dict[int, float] = {}
        self.damped = {"left": False, "right": False}
        self.play_scale = 1.0       # multiplies ramp step during playback (speed)
        self._initialised = False

        self.connected = False
        self.sdk_ok = False
        self.error: str | None = None

        self._pub = None
        self._cmd = None
        self._crc = None

        if not dry_run:
            self._init_dds()

    # ---- DDS bring-up ---------------------------------------------------- #
    def _init_dds(self) -> None:
        try:
            from unitree_sdk2py.core.channel import (  # type: ignore
                ChannelFactoryInitialize, ChannelPublisher,
            )
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_  # type: ignore
            from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_  # type: ignore
            from unitree_sdk2py.utils.crc import CRC  # type: ignore
        except Exception as exc:  # pragma: no cover
            self.error = f"SDK import failed: {exc}"
            print(f"[arm_rec] {self.error}", file=sys.stderr)
            return
        try:
            ChannelFactoryInitialize(0, self.iface)
            self._cmd = unitree_hg_msg_dds__LowCmd_()
            self._crc = CRC()
            self._cmd.motor_cmd[NOT_USED_IDX].q = 1.0  # enable arm_sdk
            self._pub = ChannelPublisher("rt/arm_sdk", LowCmd_)
            self._pub.Init()
            self.sdk_ok = True
            print(f"[arm_rec] arm_sdk publisher ready on {self.iface}")
        except Exception as exc:  # pragma: no cover
            self.error = f"arm_sdk init failed: {exc}"
            print(f"[arm_rec] {self.error}", file=sys.stderr)
            return
        self._start_lowstate_sub()

    def _start_lowstate_sub(self) -> None:
        def _run():
            from unitree_sdk2py.core.channel import ChannelSubscriber  # type: ignore
            for dotted in (
                "unitree_sdk2py.idl.unitree_hg.msg.dds_.LowState_",
                "unitree_sdk2py.idl.unitree_go.msg.dds_.LowState_",
            ):
                try:
                    mod_path, cls = dotted.rsplit(".", 1)
                    mod = __import__(mod_path, fromlist=[cls])
                    LowState_ = getattr(mod, cls)

                    def _cb(msg):
                        with self._lock:
                            for j in (*LEFT_IDX, *RIGHT_IDX):
                                try:
                                    self.joint_cur[j] = msg.motor_state[j].q
                                except Exception:
                                    pass
                            self.connected = True
                    sub = ChannelSubscriber("rt/lowstate", LowState_)
                    sub.Init(_cb, 200)
                    self._ls_sub = sub
                    return
                except Exception:
                    continue
        threading.Thread(target=_run, daemon=True).start()

    def start(self) -> None:
        # Always run the ramp loop — even in dry-run — so cmd_q advances and the
        # chase-model playback (which gates on cmd_q reaching each frame) works
        # without a live robot.  The loop no-ops the DDS Write when _pub is None.
        threading.Thread(target=self._publish_loop, daemon=True).start()

    # ---- 50 Hz publisher ------------------------------------------------- #
    def _publish_loop(self) -> None:
        while True:
            time.sleep(SEND_DT)
            try:
                self._tick_once()
            except Exception as exc:  # never let the loop die
                print(f"[arm_rec] publish error: {exc}", file=sys.stderr)

    def _tick_once(self) -> None:
        with self._lock:
            # Snap-free init: seed cmd_q from the first measured sample so the
            # arm doesn't jump when stiffness engages.
            if not self._initialised and self.joint_cur:
                for j, q in self.joint_cur.items():
                    if j in self.cmd_q:
                        self.cmd_q[j] = q
                self._initialised = True

            targets = {}
            for side, idxs in (("left", LEFT_IDX), ("right", RIGHT_IDX)):
                for k, j in enumerate(idxs):
                    targets[j] = self.slider_vals[side][k]

            step = self.step * self.play_scale
            for j, tgt in targets.items():
                cur = self.cmd_q.get(j, 0.0)
                diff = tgt - cur
                if abs(diff) <= 0.01:
                    self.cmd_q[j] = tgt
                else:
                    stp = step if diff > 0 else -step
                    if abs(stp) > abs(diff):
                        stp = diff
                    self.cmd_q[j] = cur + stp

            damped = dict(self.damped)
            cmd_q = dict(self.cmd_q)

        if self._pub is None:
            return

        # Lock the torso UPRIGHT: arm_sdk owns all 3 waist motors while enabled,
        # so we must command them — hold yaw/roll/pitch at q=0 with firm stiffness
        # (leaving them un-commanded makes them go damp and the torso droops).
        for widx in WAIST_IDX:
            m = self._cmd.motor_cmd[widx]
            m.q, m.dq, m.tau, m.kp, m.kd = 0.0, 0.0, 0.0, KP_WAIST, KD_WAIST

        for side, idxs in (("left", LEFT_IDX), ("right", RIGHT_IDX)):
            limp = damped[side]
            for j in idxs:
                mc = self._cmd.motor_cmd[j]
                mc.q = cmd_q.get(j, 0.0)
                mc.dq = 0.0
                mc.tau = 0.0
                mc.kp = 0.0 if limp else KP_STIFF
                mc.kd = 0.0 if limp else KD_STIFF
        self._cmd.crc = self._crc.Crc(self._cmd)
        self._pub.Write(self._cmd)

    # ---- UI-facing helpers ---------------------------------------------- #
    def set_joint(self, side: str, idx: int, v: float) -> None:
        with self._lock:
            self.slider_vals[side][idx] = _clamp(side, idx, float(v))

    def set_pose(self, side: str, vals: list[float]) -> None:
        with self._lock:
            self.slider_vals[side] = [_clamp(side, k, float(v)) for k, v in enumerate(vals)]

    def get_pose(self, side: str) -> list[float]:
        with self._lock:
            return list(self.slider_vals[side])

    def measured(self, side: str) -> list[float] | None:
        idxs = LEFT_IDX if side == "left" else RIGHT_IDX
        with self._lock:
            if not all(j in self.joint_cur for j in idxs):
                return None
            return [self.joint_cur[j] for j in idxs]

    def cmd_pose(self, side: str) -> list[float]:
        """Currently commanded joints (ramped toward slider_vals). Used by the
        chase-model playback to know when a frame has been physically reached."""
        idxs = LEFT_IDX if side == "left" else RIGHT_IDX
        with self._lock:
            return [self.cmd_q.get(j, 0.0) for j in idxs]

    def set_damp(self, side: str, on: bool) -> None:
        with self._lock:
            self.damped[side] = on

    def reconnect(self, iface: str) -> bool:
        """(Re)initialise DDS on ``iface``. Returns True if the publisher came up.

        Best-effort: the Unitree ChannelFactory is a per-process singleton, so
        the FIRST connect always works; switching interface after a successful
        init may need an app restart (the failure is reported via ``self.error``).
        """
        self.iface = iface
        self.dry_run = False
        self.connected = False
        self.sdk_ok = False
        self.error = None
        self._pub = None
        self._init_dds()
        return self.sdk_ok

    def close(self) -> None:
        pass


# --------------------------------------------------------------------------- #
#  Take data model + processing (ported from hands recorder, widened 6 -> 7)
# --------------------------------------------------------------------------- #
class Take:
    _counter = 0

    def __init__(self, frames: list[dict[str, list[float]]], fps: float = REC_HZ):
        Take._counter += 1
        self.name   = f"Take {Take._counter}"
        self.frames = frames
        self.fps    = fps

    @property
    def duration(self) -> float:
        return len(self.frames) / self.fps if self.fps > 0 else 0.0


def _z():
    return [0.0] * NJ


def _trim_static(frames, threshold: float = MOVE_THRESHOLD):
    if not frames:
        return frames

    def _changed(a, b):
        for side in set(a) | set(b):
            va = a.get(side, _z()); vb = b.get(side, _z())
            if any(abs(va[j] - vb[j]) > threshold for j in range(NJ)):
                return True
        return False

    start = 0
    for i in range(1, len(frames)):
        if _changed(frames[0], frames[i]):
            start = max(0, i - 1); break
    end = len(frames)
    for i in range(len(frames) - 2, start, -1):
        if _changed(frames[-1], frames[i]):
            end = min(len(frames), i + 2); break
    return frames[start:end]


def _extract_keyframes(frames, threshold: float):
    if not frames:
        return []
    keys = [0]
    for i in range(1, len(frames)):
        prev = frames[keys[-1]]; cur = frames[i]
        for side in set(prev) | set(cur):
            vp = prev.get(side, _z()); vc = cur.get(side, _z())
            if any(abs(vc[j] - vp[j]) >= threshold for j in range(NJ)):
                keys.append(i); break
    if keys[-1] != len(frames) - 1:
        keys.append(len(frames) - 1)
    return keys


def _lerp_frame(a, b, t):
    sides = set(a) | set(b)
    return {side: [a.get(side, _z())[j] * (1 - t) + b.get(side, _z())[j] * t
                   for j in range(NJ)] for side in sides}


def _smooth_linear(frames, keyframe_threshold: float, target_fps: float = REC_HZ):
    if len(frames) < 2:
        return frames
    keys = _extract_keyframes(frames, keyframe_threshold)
    if len(keys) < 2:
        return frames

    def _seg_len(fi, fj):
        a, b = frames[fi], frames[fj]; d = 0.0
        for side in set(a) | set(b):
            va = a.get(side, _z()); vb = b.get(side, _z())
            d = max(d, max(abs(vb[j] - va[j]) for j in range(NJ)))
        return max(d, 1e-3)

    total_len = sum(_seg_len(keys[i], keys[i + 1]) for i in range(len(keys) - 1))
    total_frames = max(len(frames), 2)
    out = []
    for seg in range(len(keys) - 1):
        seg_len  = _seg_len(keys[seg], keys[seg + 1])
        n_frames = max(2, round(total_frames * seg_len / total_len))
        fa, fb   = frames[keys[seg]], frames[keys[seg + 1]]
        for fi in range(n_frames - (1 if seg < len(keys) - 2 else 0)):
            t = fi / max(n_frames - 1, 1)
            out.append(_lerp_frame(fa, fb, t))
    out.append(frames[-1])
    return out


def _smooth_ema(frames, alpha: float = 0.25):
    if not frames:
        return frames
    out = [frames[0]]
    sides = set()
    for f in frames:
        sides.update(f.keys())
    prev = {s: list(frames[0].get(s, _z())) for s in sides}
    for f in frames[1:]:
        nxt = {}
        for side in sides:
            raw = f.get(side, _z())
            nxt[side] = [alpha * raw[j] + (1 - alpha) * prev[side][j] for j in range(NJ)]
        prev = nxt
        out.append(nxt)
    return out


def _compose_segments(takes, threshold: float = MOVE_THRESHOLD):
    running: dict[str, list[float]] = {}
    segments = []
    for take in takes:
        frames = take.frames
        if not frames:
            continue
        sides = set()
        for f in frames:
            sides.update(f.keys())
        for side in sides:
            running.setdefault(side, list(frames[0].get(side, _z())))
        active = {side: [False] * NJ for side in sides}
        for side in sides:
            cols = [f.get(side, _z()) for f in frames]
            for j in range(NJ):
                vals = [c[j] for c in cols]
                if max(vals) - min(vals) > threshold:
                    active[side][j] = True
        seg = []
        for f in frames:
            frame = {}
            for side in sides:
                fv = f.get(side, _z())
                frame[side] = [fv[j] if active[side][j] else running[side][j]
                               for j in range(NJ)]
            seg.append(frame)
        for side in sides:
            running[side] = list(seg[-1][side])
        segments.append(seg)
    return segments


def _compose_sequence(takes, threshold: float = MOVE_THRESHOLD):
    out = []
    for seg in _compose_segments(takes, threshold):
        out.extend(seg)
    return out


def _lerp_frame(a: dict, b: dict, t: float) -> dict:
    """Linearly interpolate between two {side: [7]} frames at fraction t∈[0,1].
    Used during playback so the commanded target glides continuously between
    recorded frames instead of stepping frame-to-frame."""
    out = {}
    for side in ("left", "right"):
        va = a.get(side); vb = b.get(side)
        if va is None and vb is None:
            continue
        if va is None:
            out[side] = list(vb); continue
        if vb is None:
            out[side] = list(va); continue
        out[side] = [va[i] * (1.0 - t) + vb[i] * t for i in range(len(va))]
    return out


def _even_speed(frames, n: int | None = None):
    if len(frames) < 2:
        return frames
    if n is None:
        n = len(frames)
    sides = set()
    for f in frames:
        sides.update(f.keys())

    def _dist(a, b):
        d = 0.0
        for side in sides:
            va = a.get(side, _z()); vb = b.get(side, _z())
            d = max(d, max(abs(vb[j] - va[j]) for j in range(NJ)))
        return d

    cum = [0.0]
    for i in range(1, len(frames)):
        cum.append(cum[-1] + _dist(frames[i - 1], frames[i]))
    total = cum[-1]
    if total <= 0:
        return frames

    def _interp(a, b, t):
        return {side: [a.get(side, _z())[j] * (1 - t) + b.get(side, _z())[j] * t
                       for j in range(NJ)] for side in sides}

    out = [frames[0]]; src = 0
    for k in range(1, n - 1):
        target = total * k / (n - 1)
        while src < len(cum) - 2 and cum[src + 1] < target:
            src += 1
        seg = cum[src + 1] - cum[src]
        t = (target - cum[src]) / seg if seg > 0 else 0.0
        out.append(_interp(frames[src], frames[src + 1], t))
    out.append(frames[-1])
    return out


def _parallel_merge(takes, fps: float = REC_HZ) -> Take:
    """Merge single-joint takes into one coordinated motion (parallel tracks)."""
    if not takes:
        return Take([], fps)
    trimmed = []
    for t in takes:
        f = _trim_static(t.frames)
        trimmed.append(f if f else t.frames)
    n = max(2, round(sum(len(f) for f in trimmed) / len(trimmed)))
    sides = set()
    for flist in trimmed:
        for frame in flist:
            sides.update(frame.keys())
    base_start = {s: list(HOME_POSE) for s in sides}
    base_end   = {s: list(HOME_POSE) for s in sides}
    for flist in trimmed:
        s0, s1 = flist[0], flist[-1]
        for side in sides:
            sv = s0.get(side, list(HOME_POSE)); ev = s1.get(side, list(HOME_POSE))
            for j in range(NJ):
                delta = abs(ev[j] - sv[j])
                if delta < MOVE_THRESHOLD:
                    continue
                cur = abs(base_end[side][j] - base_start[side][j])
                if delta > cur:
                    base_start[side][j] = sv[j]; base_end[side][j] = ev[j]
    out_frames = []
    for fi in range(n):
        t_ratio = fi / (n - 1)
        frame = {}
        for side in sides:
            sv = base_start[side]; ev = base_end[side]
            frame[side] = [sv[j] + (ev[j] - sv[j]) * t_ratio for j in range(NJ)]
        out_frames.append(frame)
    t = Take(out_frames, fps); t.name = "Combined"; return t


class SeqItem:
    def __init__(self, take: Take, children: list[Take] | None = None):
        self.take     = take
        self.children = children or []
        self._ui_checked  = False
        self._ui_expanded = False

    @property
    def is_group(self) -> bool:
        return bool(self.children)

    @property
    def info(self) -> str:
        s = f"{len(self.take.frames)} fr · {self.take.duration:.1f}s"
        if self.is_group:
            s += f"  ·  {len(self.children)} tracks"
        return s


# --------------------------------------------------------------------------- #
#  Flet UI helpers (ported)
# --------------------------------------------------------------------------- #
def _border(w=1, color=None):
    color = color or BORDER
    s = ft.BorderSide(w, color)
    return ft.Border(top=s, right=s, bottom=s, left=s)


def _apply_seg_style(btn, selected: bool):
    """Inverted selection look: a SELECTED segmented-button is dark-filled with
    ACCENT text + a bright ACCENT border; UNSELECTED is a plain grey fill with
    white text. (Was accent-fill + black text — now flipped, per request.)"""
    btn.bgcolor = BG if selected else NEUTRAL
    btn.content.color = ACCENT if selected else "#ffffff"
    btn.elevation = 0 if selected else 3
    btn.style.side = (ft.BorderSide(2, ACCENT) if selected
                      else ft.BorderSide(1, BORDER))


def _seg_button(label, value, on_click, *, size=13, height=36, expand=False,
                selected=False) -> ft.Button:
    """A single segmented/toggle button styled by _apply_seg_style."""
    btn = ft.Button(
        content=ft.Text(label.upper(), size=size, weight=ft.FontWeight.W_900,
                        no_wrap=True, overflow=ft.TextOverflow.VISIBLE),
        height=height, expand=expand, data=value, on_click=on_click,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6),
                             padding=ft.Padding(8, 0, 8, 0)))
    _apply_seg_style(btn, selected)
    return btn


def _toggle_group(options: list[str], selected_ref: list[str], on_select,
                  *, size=13, height=36) -> ft.Row:
    btns: list[ft.Button] = []

    def _make(opt):
        def _click(e, v=opt, bs=btns):
            selected_ref[0] = v
            for b in bs:
                _apply_seg_style(b, b.data == v)
                b.update()
            on_select(v)
        btn = _seg_button(opt, opt, _click, size=size, height=height,
                          selected=(opt == selected_ref[0]))
        btns.append(btn)
        return btn

    return ft.Row([_make(o) for o in options], spacing=6, tight=True, wrap=True)


def _pill(text, on_click, color, *, expand=False, width=None, height=40, size=15,
          text_color="#ffffff"):
    return ft.Button(
        content=ft.Text(text.upper(), size=size, weight=ft.FontWeight.W_900,
                        color=text_color, text_align=ft.TextAlign.CENTER,
                        no_wrap=True, overflow=ft.TextOverflow.VISIBLE),
        on_click=on_click, width=width, height=height, expand=expand,
        bgcolor=color, elevation=4,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=6),
            padding=ft.Padding(4, 0, 4, 0),
            shadow_color=ft.Colors.with_opacity(0.55, "#000000"),
            elevation={"": 4, "hovered": 8, "pressed": 1}))


def _card(title, *controls):
    kids = []
    if title:
        kids.append(ft.Text(title.upper(), size=17, weight=ft.FontWeight.W_900,
                            color=ACCENT))
    kids.extend(controls)
    return ft.Container(
        content=ft.Column(kids, spacing=6),
        bgcolor=CARD2, border_radius=16, padding=ft.Padding(12, 10, 12, 10),
        border=_border(1, BORDER),
        shadow=ft.BoxShadow(blur_radius=16, spread_radius=0,
                            color=ft.Colors.with_opacity(0.45, "#0a1830"),
                            offset=ft.Offset(0, 6)),
        margin=ft.Margin(0, 0, 0, 0))


# --------------------------------------------------------------------------- #
#  Home-pose persistence
# --------------------------------------------------------------------------- #
def _load_home() -> dict[str, list[float]]:
    try:
        d = json.loads(HOME_FILE.read_text())
        return {"left": [float(x) for x in d["left"]],
                "right": [float(x) for x in d["right"]]}
    except Exception:
        return {"left": list(HOME_POSE), "right": list(HOME_POSE)}


def _save_home(home: dict[str, list[float]]) -> None:
    try:
        HOME_FILE.parent.mkdir(parents=True, exist_ok=True)
        HOME_FILE.write_text(json.dumps(home, indent=2))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  App
# --------------------------------------------------------------------------- #
class RecorderApp:
    def __init__(self, page: ft.Page, iface: str, step: float, dry_run: bool):
        self.page = page
        self.iface = iface
        self.channel = ArmChannel(iface, step=step, dry_run=dry_run)
        self.channel.start()

        self.home = _load_home()
        # Do NOT drive the arms to home on startup — seed the sliders from the
        # arm's measured pose if we have it, else the stored home (UI only).
        for side in ("left", "right"):
            m = self.channel.measured(side)
            self.channel.set_pose(side, m if m else self.home[side])

        self.slider_vals = {s: self.channel.get_pose(s) for s in ("left", "right")}
        self.sliders:  dict[str, list[ft.Slider]] = {}
        self.val_lbls: dict[str, list[ft.Text]]   = {}
        self.arm_cards: dict[str, ft.Container]    = {}
        self.conn_lbl: ft.Text | None = None

        self._recording  = False
        self._rec_frames: list[dict[str, list[float]]] = []
        self._rec_start  = 0.0
        self._rec_last_t = 0.0

        self._playing = False
        self._play_segments: list[list[dict[str, list[float]]]] = []
        self._play_seg_idx = 0
        self._play_frame_ticks = 0
        self._play_phase = "play"   # play → reach → settle
        self._play_fps = float(REC_HZ)
        self._play_start_t = 0.0
        self._play_dwell_until = 0.0

        self.sequence: list[SeqItem] = []

        self.speed_mult = 1.0
        self.smooth_mode = "Linear"
        self.smooth_strength = 0.5
        self.arm = "both"           # both arms active (no arm selector in this UI)
        self.direction = "up"
        self.gesture = GESTURES[0]
        self._gesture_items: list[str] = list(GESTURES)

        # Hold the arm's real pose at startup instead of homing it. In dry-run
        # there's no feedback, so nothing to seed.
        self._seeded = dry_run

        self._build()
        self._apply_arm_enable()
        self.page.run_task(self._loop_async)

    # ------------------------------------------------------------------ #
    #  Build UI
    # ------------------------------------------------------------------ #
    def _build(self):
        p = self.page
        p.title = "G1 Arm Animation Recorder"
        p.theme_mode = ft.ThemeMode.DARK
        p.bgcolor = BG
        p.padding = 0
        p.spacing = 0
        p.scroll = None
        p.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
        p.theme = ft.Theme(slider_theme=ft.SliderTheme(track_height=6))
        p.window.maximized = True

        # ── title bar ──────────────────────────────────────────────────────
        title_bar = ft.Text("UNITREE G1  ·  ARM TRAJECTORY RECORDER",
                            size=18, weight=ft.FontWeight.W_900, color=ACCENT)

        # ── arm sliders ────────────────────────────────────────────────────
        left_arm_col  = self._arm_card("left")
        right_arm_col = self._arm_card("right")
        left_arm_col.expand  = True
        right_arm_col.expand = True
        arms_row = ft.Row([left_arm_col, right_arm_col], spacing=8,
                          vertical_alignment=ft.CrossAxisAlignment.START)

        # ── two floating cards: AUTO-SMOOTH + PRESET ───────────────────────
        # AUTO-SMOOTH card — manual toggle buttons (no wrap) + strength slider
        self._smooth_btns: dict[str, ft.Button] = {}
        def _smooth_btn(label):
            val = label
            sel = (val == self.smooth_mode)
            btn = ft.Button(
                content=ft.Text(label.upper(), size=12, weight=ft.FontWeight.W_900,
                                color="#000000" if sel else "#ffffff", no_wrap=True),
                height=32, data=val,
                on_click=lambda e, v=val: self._on_smooth_mode(v),
                bgcolor=ACCENT if sel else NEUTRAL, elevation=2,
                style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6),
                                     padding=ft.Padding(14, 0, 14, 0)))
            self._smooth_btns[val] = btn
            return btn
        smooth_tog = ft.Row(
            [_smooth_btn("None"), _smooth_btn("Linear"), _smooth_btn("EMA")],
            spacing=6, tight=True)
        self.smooth_val = ft.Text(f"{self.smooth_strength:.2f}", size=16, color=FG_VAL,
                                  weight=ft.FontWeight.W_900, width=52,
                                  text_align=ft.TextAlign.RIGHT)
        smooth_slider = ft.Slider(min=0.0, max=1.0, value=self.smooth_strength, expand=True,
                                  active_color=SLIDER_ACTIVE, thumb_color=SLIDER_THUMB,
                                  on_change=self._on_smooth_strength)
        smooth_col = ft.Column([
            ft.Text("AUTO-SMOOTH  ·  APPLIED ON STOP", size=15,
                    weight=ft.FontWeight.W_900, color=ACCENT),
            ft.Row([smooth_tog,
                    ft.Text("STRENGTH", color=FG_DIM, size=15, weight=ft.FontWeight.W_900),
                    smooth_slider, self.smooth_val],
                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=8, tight=True)
        smooth_card = ft.Container(
            content=smooth_col, expand=1,
            bgcolor=CARD2, border_radius=14, padding=ft.Padding(10, 8, 10, 8),
            border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        # PRESET card
        self.preset_list = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

        self._custom_gesture_field = ft.TextField(
            hint_text="NEW PRESET NAME…", dense=True, filled=True,
            bgcolor=CARD, border_radius=10, border_color=BORDER,
            content_padding=ft.Padding(12, 6, 12, 6),
            text_size=14, color=FG, text_style=ft.TextStyle(weight=ft.FontWeight.W_700),
            hint_style=ft.TextStyle(color=FG_DIM, weight=ft.FontWeight.W_600),
            expand=True, on_submit=self._add_gesture)

        self.speed_val = ft.Text(f"×{self.speed_mult:.2f}", size=16, color=FG_VAL,
                                 weight=ft.FontWeight.W_900, width=62)
        speed_slider = ft.Slider(min=0.25, max=20.0, value=self.speed_mult, expand=True,
                                 active_color=SPEED_ACTIVE, thumb_color=SPEED_THUMB,
                                 on_change=self._on_speed)

        save_col = ft.Column([
            ft.Text("PRESET", color=ACCENT, size=17, weight=ft.FontWeight.W_900),
            ft.Container(self.preset_list, expand=True),
            ft.Row([self._custom_gesture_field,
                    _pill("+ Add", self._add_gesture, ACCENT, height=36, width=110, text_color="#000000"),
                    _pill("✕ Remove", self._remove_gesture, DANGER, height=36, width=150)],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([
                _pill("Save Preset", self._save_preset,         NEUTRAL, height=34, expand=True),
                _pill("▶ Play",      self._play_from_final,     NEUTRAL, height=34, expand=True),
                _pill("◀ Reverse",   self._play_from_final_rev, NEUTRAL, height=34, expand=True),
            ], spacing=6),
            ft.Row([ft.Text("SPEED", color=FG_DIM, size=15, weight=ft.FontWeight.W_900),
                    speed_slider, self.speed_val,
                    _pill("Save @ Speed", self._save_at_speed, PURPLE, height=34)],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=10, expand=True)
        save_card = ft.Container(
            content=save_col, expand=4,
            bgcolor=CARD2, border_radius=14, padding=ft.Padding(10, 8, 10, 8),
            border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        # ── right column: pose bar, record, sequence ───────────────────────
        self.conn_lbl = ft.Text("", size=11, weight=ft.FontWeight.W_900, color=FG_DIM)

        # Network connect: choose an ethernet/wifi interface and (re)connect DDS.
        self.net_dd = ft.Dropdown(
            options=[], dense=True, filled=True, bgcolor=CARD, border_radius=8,
            border_color=BORDER, text_size=12, color=FG, expand=True,
            hint_text="SELECT INTERFACE…",
            hint_style=ft.TextStyle(color=FG_DIM, weight=ft.FontWeight.W_600))
        net_row = ft.Row([
            ft.Text("NET", color=FG_DIM, size=12, weight=ft.FontWeight.W_900, width=32),
            self.net_dd,
            _pill("⟳", self._refresh_net, NEUTRAL, height=32, width=40, size=13),
            _pill("Connect", self._on_net_connect, ACCENT, height=32, width=90,
                  size=11, text_color="#000000"),
        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        pose_bar = ft.Column([
            net_row,
            ft.Row([
                _pill("⌂ Default Pos", self._default_both, ACCENT,
                      expand=True, height=32, size=11, text_color="#000000"),
                _pill("Set As Home", self._set_home, PURPLE,
                      expand=True, height=32, size=11),
            ], spacing=6),
            self.conn_lbl,
        ], spacing=6)

        self.rec_btn = _pill("● REC", self._start_rec, DANGER, expand=True, height=34, size=12)
        self.rec_btn.bgcolor = DANGER
        self.stop_btn = _pill("■ STOP", self._stop_rec, NEUTRAL, expand=True, height=34, size=12)
        self.stop_btn.disabled = True
        self.rec_info = ft.Text("0 fr · 0.0s", size=13, color=FG_VAL,
                                weight=ft.FontWeight.W_900, text_align=ft.TextAlign.LEFT)
        rec_section = ft.Column([
            ft.Row([
                ft.Text("RECORDING", size=13, weight=ft.FontWeight.W_900, color=ACCENT),
                ft.Container(expand=True),
                self.rec_info,
            ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([self.rec_btn, self.stop_btn], spacing=6,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=6)

        self.seq_col = ft.Column([], spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
        self.empty_lbl = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.LAYERS_OUTLINED, color=EMPTY_ICON, size=14),
                ft.Text("NO TAKES YET", color=EMPTY_TEXT, size=12, weight=ft.FontWeight.W_800),
            ], spacing=6),
            bgcolor=EMPTY_BG, border_radius=8, padding=ft.Padding(10, 8, 10, 8),
            border=_border(1, EMPTY_BORDER))
        self.seq_col.controls.append(self.empty_lbl)

        seq_actions = ft.Column([
            ft.Row([
                _pill("▶ PLAY",   self._play_selected,     ACCENT,  expand=True, height=30, size=11, text_color="#000000"),
                _pill("◀ REV",    self._play_selected_rev, TEAL,    expand=True, height=30, size=11, text_color="#000000"),
                _pill("⊕ COMB",  self._combine_selected,  INDIGO,  expand=True, height=30, size=11),
            ], spacing=4),
            ft.Row([
                _pill("⊘ SPLIT", self._uncombine_selected, PURPLE,  expand=True, height=30, size=11),
                _pill("✕ DEL",   self._delete_selected,    DANGER,  expand=True, height=30, size=11),
                _pill("CLEAR",   self._clear_all,          NEUTRAL, expand=True, height=30, size=11),
            ], spacing=4),
        ], spacing=4)

        seq_section = ft.Column([
            ft.Text("SEQUENCE  ·  PLAYS TOP → BOTTOM", size=13,
                    weight=ft.FontWeight.W_900, color=ACCENT),
            self.seq_col,
            seq_actions,
        ], spacing=8)

        # ── status (messages only, not shown in layout) ────────────────────
        self.status = ft.Text("", size=13, color=FG_DIM, weight=ft.FontWeight.W_600)

        pose_card = ft.Container(
            content=pose_bar,
            bgcolor=CARD2, border_radius=14, padding=ft.Padding(8, 8, 8, 8),
            border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        rec_card = ft.Container(
            content=rec_section,
            bgcolor=CARD2, border_radius=14, padding=ft.Padding(10, 8, 10, 8),
            border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        seq_card = ft.Container(
            content=ft.Column([
                ft.Text("SEQUENCE  ·  PLAYS TOP → BOTTOM", size=13,
                        weight=ft.FontWeight.W_900, color=ACCENT),
                ft.Container(self.seq_col, expand=True),
                seq_actions,
            ], spacing=8, expand=True),
            bgcolor=CARD2, border_radius=16, padding=10, border=_border(1, BORDER),
            expand=True,
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        title_card = ft.Container(
            content=title_bar,
            bgcolor=CARD2, border_radius=12, padding=ft.Padding(14, 8, 14, 8),
            border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        # ── assemble: left column (3x) + right column (1x) ─────────────────
        left_col = ft.Column(
            [arms_row, smooth_card, save_card],
            spacing=8, expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        right_col = ft.Column(
            [pose_card, rec_card, seq_card],
            spacing=8, expand=True)

        main_row = ft.Row(
            [ft.Container(left_col, expand=3, bgcolor=BG),
             ft.Container(right_col, expand=1)],
            spacing=12, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH)

        inner = ft.Column([title_card, main_row], spacing=8, expand=True,
                          horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        p.add(ft.Container(inner, padding=ft.Padding(12, 8, 12, 8), expand=True, bgcolor=BG))

        self._rebuild_seq()
        self._rebuild_preset_list()
        self._refresh_net()

    # ------------------------------------------------------------------ #
    #  Network connect (ethernet / wifi dropdown)
    # ------------------------------------------------------------------ #
    def _refresh_net(self, e=None):
        """Populate the interface dropdown; flag which are on the G1 network."""
        ifaces = _list_net_ifaces()
        opts = []
        for nf in ifaces:
            tag = "wifi" if nf["kind"] == "wifi" else "eth"
            ip = nf["ip"] or "no IP"
            mark = "  ✓ G1 net" if nf["on_g1"] else ("" if nf["up"] else "  (down)")
            opts.append(ft.dropdown.Option(
                key=nf["name"], text=f"{nf['name']}  ·  {tag}  ·  {ip}{mark}"))
        self.net_dd.options = opts
        # Preselect: current iface if present, else the first G1-network iface.
        names = [nf["name"] for nf in ifaces]
        on_g1 = next((nf["name"] for nf in ifaces if nf["on_g1"]), None)
        if self.iface in names:
            self.net_dd.value = self.iface
        elif on_g1:
            self.net_dd.value = on_g1
        self._net_cache = {nf["name"]: nf for nf in ifaces}
        self._set_status(f"Found {len(ifaces)} interface(s)"
                         + (f" — {on_g1} is on the G1 network" if on_g1 else
                            " — none on the G1 network (192.168.123.x)"))

    def _on_net_connect(self, e=None):
        name = self.net_dd.value
        if not name:
            self._set_status("Pick a network interface first."); return
        nf = getattr(self, "_net_cache", {}).get(name, {})
        if not nf.get("up"):
            self._set_status(f"{name} is DOWN — plug in the cable / enable the adapter."); return
        if not nf.get("on_g1"):
            self._set_status(
                f"{name} ({nf.get('ip') or 'no IP'}) is NOT on the G1 network "
                f"(192.168.123.x / 124.x / 10.42.x). Connect to the same network first.")
            return
        self._set_status(f"Connecting on {name} ({nf.get('ip')})…")
        ok = self.channel.reconnect(name)
        self.iface = name
        if ok:
            self._set_status(f"● Connected on {name} ({nf.get('ip')}) — arm_sdk up")
        else:
            self._set_status(f"Connect failed on {name}: {self.channel.error or 'unknown'}")
        self._safe_update()

    def _arm_card(self, side: str) -> ft.Container:
        rows = []
        self.sliders[side] = []
        self.val_lbls[side] = []
        lims = _limits(side)
        idxs = LEFT_IDX if side == "left" else RIGHT_IDX
        for i, name in enumerate(JOINT_LABELS):
            lo, hi = lims[i]
            # Symmetric display range so 0 (neutral) sits at the slider's center.
            # Real URDF limits [lo, hi] are still enforced via _clamp on send.
            rng = max(abs(lo), abs(hi))
            v0 = self.slider_vals[side][i]
            ep_lo, ep_hi = JOINT_ENDPOINTS[i]
            val = ft.Text(f"{v0:+.3f}", size=12, color=FG_VAL, weight=ft.FontWeight.W_900,
                          width=50, text_align=ft.TextAlign.CENTER)
            sld = ft.Slider(min=-rng, max=rng, value=v0, expand=True,
                            active_color=SLIDER_ACTIVE, thumb_color=SLIDER_THUMB,
                            on_change=lambda e, s=side, idx=i: self._on_slider(s, idx, e))
            self.sliders[side].append(sld)
            self.val_lbls[side].append(val)
            # joint name + index on a single line
            lbl = ft.Row(
                [ft.Text(name, size=10, color=LBL, weight=ft.FontWeight.W_900, no_wrap=True),
                 ft.Text(f"[{idxs[i]}]", size=9, color=FG_DIM, no_wrap=True)],
                spacing=3, tight=True)
            # endpoint labels sit on each end of the slider
            ep_lo_lbl = ft.Text(ep_lo, size=9, color=FG_DIM, weight=ft.FontWeight.W_700,
                                width=40, text_align=ft.TextAlign.RIGHT, no_wrap=True)
            ep_hi_lbl = ft.Text(ep_hi, size=9, color=FG_DIM, weight=ft.FontWeight.W_700,
                                width=40, text_align=ft.TextAlign.LEFT, no_wrap=True)
            rows.append(ft.Row(
                [ft.Container(lbl, width=132),
                 ep_lo_lbl, sld, ep_hi_lbl, val],
                spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        self._damp_refs = getattr(self, "_damp_refs", {})
        damp_btn = _pill("Damp", lambda e, s=side: self._toggle_damp(s), NEUTRAL,
                         expand=True, height=28, size=11)
        self._damp_refs[side] = damp_btn
        btns = ft.Row([
            _pill("Home", lambda e, s=side: self._home_one(s), ACCENT, expand=True, height=28, size=11, text_color="#000000"),
            _pill("Mirror →", lambda e, s=side: self._mirror(s), GREEN, expand=True, height=28, size=11, text_color="#000000"),
            damp_btn,
        ], spacing=4)

        title_text = ft.Text(f"{side.upper()} ARM", size=15, weight=ft.FontWeight.W_900,
                             color=ACCENT)

        col = ft.Column([title_text, *rows, btns], spacing=2, tight=True)
        self.arm_cards[side] = col
        card = ft.Container(
            content=col,
            bgcolor=CARD2, border_radius=14, padding=ft.Padding(10, 8, 10, 8),
            border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))
        return card

    # ------------------------------------------------------------------ #
    #  Sequence list rendering (ported)
    # ------------------------------------------------------------------ #
    def _rebuild_seq(self):
        self.seq_col.controls.clear()
        if not self.sequence:
            self.seq_col.controls.append(self.empty_lbl)
            self._safe_update(); return
        for idx, item in enumerate(self.sequence):
            self.seq_col.controls.append(self._seq_row(idx, item))
            if item.is_group and item._ui_expanded:
                for ct in item.children:
                    self.seq_col.controls.append(
                        ft.Container(
                            ft.Row([ft.Text("├", color=FG_DIM, size=13),
                                    ft.Text(ct.name, color=FG, size=13, width=150),
                                    ft.Text(f"{len(ct.frames)} fr", color=FG_DIM, size=12)],
                                   spacing=8),
                            padding=ft.Padding(28, 0, 0, 0)))
        self._safe_update()

    def _seq_row(self, idx: int, item: SeqItem) -> ft.Container:
        cb = ft.Checkbox(value=item._ui_checked, fill_color=ACCENT,
                         on_change=lambda e, it=item: setattr(it, "_ui_checked", e.control.value))
        left = [cb]
        if item.is_group:
            left.append(ft.IconButton(
                icon=ft.Icons.EXPAND_MORE if item._ui_expanded else ft.Icons.CHEVRON_RIGHT,
                icon_color=FG_DIM, icon_size=20,
                on_click=lambda e, it=item: self._toggle_expand(it)))
        else:
            left.append(ft.Container(width=32))

        name = ft.TextField(value=item.take.name, dense=True, filled=True,
                            bgcolor=CARD, border_radius=10, width=150,
                            border_color="transparent", content_padding=10,
                            text_size=13, color=FG,
                            on_change=lambda e, it=item: setattr(it.take, "name", e.control.value))
        info = ft.Text(item.info, color=FG_DIM, size=12, width=150)

        drag_handle = ft.Icon(ft.Icons.DRAG_HANDLE, color=FG_DIM, size=20)
        row_content = ft.Container(
            content=ft.Row([drag_handle, *left, name, info, ft.Container(expand=True)],
                           spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ITEM, border_radius=12, border=_border(1, BORDER),
            padding=ft.Padding(10, 6, 8, 6))

        ghost = ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.DRAG_HANDLE, color=FG, size=20),
                            ft.Text(item.take.name, color=FG, size=15, weight=ft.FontWeight.W_900),
                            ft.Text(f"  {item.info}", color=FG_DIM, size=13)],
                           spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ACCENT, border_radius=12, padding=ft.Padding(14, 10, 14, 10),
            opacity=0.75, width=600)

        draggable = ft.Draggable(
            group="takes", content=row_content,
            content_when_dragging=ft.Container(height=6, bgcolor=ACCENT, border_radius=3, opacity=0.4),
            content_feedback=ghost, data=idx)

        target_ref = ft.Ref[ft.Container]()

        def _on_will_accept(e, ref=target_ref):
            ref.current.border = _border(2, ACCENT); ref.current.update()

        def _on_leave(e, ref=target_ref):
            ref.current.border = _border(1, BORDER); ref.current.update()

        def _on_accept(e, to_idx=idx, ref=target_ref):
            try:
                from_idx = int(e.src.data) if e.src and e.src.data is not None else None
            except (TypeError, ValueError):
                from_idx = None
            ref.current.border = _border(1, BORDER)
            if from_idx is not None and from_idx != to_idx:
                self.sequence.insert(to_idx, self.sequence.pop(from_idx))
                self._rebuild_seq()

        return ft.Container(
            ref=target_ref,
            content=ft.DragTarget(group="takes", content=draggable,
                                  on_will_accept=_on_will_accept, on_leave=_on_leave,
                                  on_accept=_on_accept),
            border_radius=12, border=_border(1, BORDER))

    def _toggle_expand(self, item: SeqItem):
        item._ui_expanded = not item._ui_expanded
        self._rebuild_seq()

    def _selected(self) -> list[int]:
        return [i for i, it in enumerate(self.sequence) if it._ui_checked]

    # ------------------------------------------------------------------ #
    #  Tick loop (Flet event loop)
    # ------------------------------------------------------------------ #
    async def _loop_async(self):
        import asyncio
        while True:
            now = time.monotonic()
            dirty = False

            # One-time seed: once the robot reports its joints, snap the sliders
            # (and the ramp target) to the measured pose so the arm HOLDS where
            # it is at startup instead of driving to home.
            if not self._seeded and not self._recording and not self._playing:
                mL, mR = self.channel.measured("left"), self.channel.measured("right")
                if mL is not None and mR is not None:
                    self._apply_pose("left", mL)
                    self._apply_pose("right", mR)
                    self._seeded = True
                    dirty = True

            if self._recording and (now - self._rec_last_t) >= REC_DT:
                self._rec_frames.append({s: self.channel.get_pose(s) for s in ("left", "right")})
                self._rec_last_t = now
                self.rec_info.value = f"{len(self._rec_frames)} fr · {now - self._rec_start:.1f}s"
                dirty = True

            if self._playing and self._play_segments:
                # Ported from the hands recorder: within a take, frames advance on
                # the wall clock scaled by the speed multiplier (so speed changes
                # the TAKE speed and 1.0× plays at recorded timing). Between takes
                # we (a) wait for the arm to physically finish the current take,
                # then (b) hold a FIXED settle — so a faster speed never shrinks
                # the gap between takes or lets the next take start early.
                seg = self._play_segments[self._play_seg_idx]
                if self._play_phase == "play":
                    elapsed = now - self._play_start_t
                    # Continuous (fractional) frame position → interpolate between
                    # recorded frames so the target glides at the tick rate instead
                    # of snapping to each 25 Hz frame (that snapping was the audible
                    # motor stepping). This makes playback as smooth as the Home ramp.
                    fpos = elapsed * self._play_fps * self.speed_mult
                    last = len(seg) - 1
                    if fpos >= last:
                        self._render(seg[-1])
                        self._play_phase = "reach"
                        self._play_frame_ticks = 0
                    else:
                        fi = int(fpos)
                        frac = fpos - fi
                        target = _lerp_frame(seg[fi], seg[fi + 1], frac)
                        self._render(self._smooth_live(target))
                    dirty = True
                elif self._play_phase == "reach":
                    self._render(seg[-1])
                    self._play_frame_ticks += 1
                    if self._reached(seg[-1]) or self._play_frame_ticks >= PLAY_FRAME_TIMEOUT:
                        self._play_dwell_until = now + SEGMENT_SETTLE_SEC
                        self._play_phase = "settle"
                    dirty = True
                else:  # settle — fixed hold, independent of speed
                    self._render(seg[-1])
                    if now >= self._play_dwell_until:
                        self._play_seg_idx += 1
                        if self._play_seg_idx >= len(self._play_segments):
                            self._playing = False
                            self.channel.play_scale = 1.0
                            self._set_status("Playback done")
                        else:
                            self._play_start_t = now
                            self._play_phase = "play"
                    dirty = True

            # connection pill
            if self.conn_lbl is not None:
                if self.channel.dry_run:
                    self.conn_lbl.value = "○ DRY-RUN (no DDS)"; self.conn_lbl.color = FG_DIM
                elif self.channel.connected:
                    self.conn_lbl.value = f"● LIVE · {self.iface}"; self.conn_lbl.color = OK
                elif self.channel.sdk_ok:
                    self.conn_lbl.value = f"◐ NO FEEDBACK · {self.iface}"; self.conn_lbl.color = TEAL
                else:
                    self.conn_lbl.value = f"○ {(self.channel.error or 'NO ARM_SDK')[:40]}"
                    self.conn_lbl.color = ERR

            if dirty:
                self._safe_update()
            await asyncio.sleep(0.04 if not self._playing else 0.02)

    def _smooth_live(self, frame: dict) -> dict:
        """Apply the AUTO-SMOOTH slider LIVE during playback (like speed): an EMA
        low-pass on the commanded target. Higher strength = smoother/laggier.
        Mode 'None' passes through untouched."""
        if getattr(self, "smooth_mode", "None") == "None":
            self._play_ema = None
            return frame
        # strength 0..1 → EMA weight on the previous sample 0.15..0.85
        a = 0.15 + 0.70 * float(getattr(self, "smooth_strength", 0.5))
        prev = getattr(self, "_play_ema", None)
        if prev is None:
            self._play_ema = {s: list(v) for s, v in frame.items()}
            return frame
        out = {}
        for s, v in frame.items():
            p = prev.get(s, v)
            out[s] = [a * p[i] + (1.0 - a) * v[i] for i in range(len(v))]
        self._play_ema = out
        return out

    def _render(self, f: dict[str, list[float]]):
        for side, vals in f.items():
            if side not in ("left", "right"):
                continue
            self.channel.set_pose(side, vals)
            self.slider_vals[side] = list(vals)
            for d, v in enumerate(vals):
                self.sliders[side][d].value = v
                self.val_lbls[side][d].value = f"{v:+.3f}"

    def _start_segments(self, segments, fps, status):
        segments = [s for s in segments if s]
        if not segments:
            return
        self._play_segments = segments
        self._play_seg_idx = 0
        self._play_frame_ticks = 0
        self._play_phase = "play"
        self._play_fps = fps
        self._play_dwell_until = 0.0
        self._playing = True
        self._play_start_t = time.monotonic()
        self._play_ema = None    # reset live-smoothing EMA at each playback start
        # Tight follow so the arm tracks the wall-clock frames without lag; the
        # take speed itself comes from the timeline (self.speed_mult in _loop_async).
        self.channel.play_scale = FOLLOW_SCALE
        self._set_status(status)

    def _reached(self, frame: dict[str, list[float]]) -> bool:
        """True once the arm's commanded joints match ``frame`` within PLAY_TOL
        (both sides). Targets are clamped the same way _render clamps them, so a
        frame that asks for an out-of-range angle still counts as reached."""
        for side, vals in frame.items():
            if side not in ("left", "right"):
                continue
            cur = self.channel.cmd_pose(side)
            for k in range(NJ):
                tgt = _clamp(side, k, vals[k])
                if abs(cur[k] - tgt) > PLAY_TOL:
                    return False
        return True

    # ------------------------------------------------------------------ #
    #  Slider / pose events
    # ------------------------------------------------------------------ #
    def _on_slider(self, side, idx, e):
        # Slider range is symmetric for centering; clamp to the real URDF limit so
        # the readout and commanded value never exceed the joint's actual range.
        v = _clamp(side, idx, float(e.control.value))
        self.slider_vals[side][idx] = v
        self.channel.set_joint(side, idx, v)
        self.val_lbls[side][idx].value = f"{v:+.3f}"
        self.val_lbls[side][idx].update()

    def _apply_pose(self, side, vals):
        for d in range(NJ):
            v = _clamp(side, d, vals[d])
            self.slider_vals[side][d] = v
            self.channel.set_joint(side, d, v)
            self.sliders[side][d].value = v
            self.val_lbls[side][d].value = f"{v:+.3f}"

    def _default_both(self, e=None):
        for side in ("left", "right"):
            self._apply_pose(side, self.home[side])
        self._set_status("both arms → home / default position")

    def _home_one(self, side):
        self._apply_pose(side, self.home[side])
        self._set_status(f"{side} → home")

    def _set_home(self, e=None):
        for side in ("left", "right"):
            self.home[side] = list(self.slider_vals[side])
        _save_home(self.home)
        self._set_status("saved current pose as home (both arms)")

    def _mirror(self, source):
        target = "right" if source == "left" else "left"
        vals = []
        for d in range(NJ):
            v = self.slider_vals[source][d]
            if d in MIRROR_FLIP:
                v = -v
            vals.append(_clamp(target, d, v))
        self._apply_pose(target, vals)
        self._set_status(f"mirrored {source} → {target}")

    def _toggle_damp(self, side):
        on = not self.channel.damped[side]
        self.channel.set_damp(side, on)
        btn = self._damp_refs[side]
        btn.bgcolor = DANGER if on else NEUTRAL
        btn.update()
        # when re-stiffening, seed sliders from measured so it holds where it is
        if not on:
            m = self.channel.measured(side)
            if m:
                self._apply_pose(side, m)
        self._set_status(f"{side} arm {'DAMPED (limp)' if on else 'stiff (holding)'}")

    # ------------------------------------------------------------------ #
    #  Smooth / speed / arm-select handlers
    # ------------------------------------------------------------------ #
    def _on_smooth_strength(self, e):
        self.smooth_strength = float(e.control.value)
        self.smooth_val.value = f"{self.smooth_strength:.2f}"
        self.smooth_val.update()

    def _on_speed(self, e):
        self.speed_mult = float(e.control.value)
        self.speed_val.value = f"×{self.speed_mult:.2f}"
        self.speed_val.update()

    def _apply_arm_enable(self):
        for side in ("left", "right"):
            active = (self.arm == "both") or (self.arm == side)
            for sld in self.sliders.get(side, []):
                sld.disabled = not active
            card = self.arm_cards.get(side)
            if card is not None:
                card.opacity = 1.0 if active else 0.35
                card.disabled = not active
        self._safe_update()

    # ------------------------------------------------------------------ #
    #  AUTO-SMOOTH card
    # ------------------------------------------------------------------ #
    def _on_smooth_mode(self, v):
        """Select the smoothing mode and restyle the toggle buttons."""
        self.smooth_mode = v
        for name, btn in self._smooth_btns.items():
            sel = (name == v)
            btn.bgcolor = ACCENT if sel else NEUTRAL
            btn.content.color = "#000000" if sel else "#ffffff"
            btn.update()
        self._set_status(f"Auto-smooth → {v}")

    # ------------------------------------------------------------------ #
    #  PRESET card
    # ------------------------------------------------------------------ #
    def _current_frames(self) -> list[dict[str, list[float]]]:
        """Merge the whole sequence into one frame list, smoothed + evened out."""
        if not self.sequence:
            return []
        frames = _compose_sequence([it.take for it in self.sequence])
        frames = self._apply_smoothing(frames)
        return _even_speed(frames)

    def _list_saved_presets(self) -> set[str]:
        """Names of presets actually saved on disk (folders with animation.yaml)."""
        out: set[str] = set()
        try:
            for d in PRESETS_DIR.iterdir():
                if d.is_dir() and (d / "animation.yaml").exists():
                    out.add(d.name)
        except OSError:
            pass
        return out

    def _rebuild_preset_list(self):
        """Render the scrollable preset list, disk-backed: permanent defaults +
        every preset saved on disk + any in-memory (unsaved) names. A ✓ marks
        presets that actually have an animation.yaml."""
        saved = self._list_saved_presets()
        # Ordered, de-duped: permanents first, then saved customs, then unsaved.
        ordered: list[str] = list(GESTURES)
        for n in sorted(saved | set(self._gesture_items)):
            if n not in ordered:
                ordered.append(n)
        self._gesture_items = ordered
        if self.gesture not in ordered and ordered:
            self.gesture = ordered[0]

        self.preset_list.controls.clear()
        for name in ordered:
            sel  = name == self.gesture
            perm = name in PERMANENT
            has_yaml = name in saved
            def _click(e, v=name):
                self.gesture = v
                self._rebuild_preset_list()
                self._safe_update()
            row = ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.LOCK if perm else ft.Icons.LABEL_OUTLINE,
                            size=15, color="#000000" if sel else FG_DIM),
                    ft.Text(name, size=14, weight=ft.FontWeight.W_900,
                            color="#000000" if sel else FG, expand=True),
                    ft.Text("✓ saved" if has_yaml else "unsaved", size=11,
                            weight=ft.FontWeight.W_800,
                            color=("#000000" if sel else (OK if has_yaml else FG_DIM))),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                on_click=_click,
                bgcolor=ACCENT if sel else ITEM,
                border_radius=8, padding=ft.Padding(12, 8, 12, 8),
                border=_border(1, BORDER))
            self.preset_list.controls.append(row)
        self._safe_update()

    def _add_gesture(self, e=None):
        name = (self._custom_gesture_field.value or "").strip().replace(" ", "_").lower()
        if not name:
            return
        if not self.sequence:
            self._set_status("Add some takes before creating a preset."); return
        if name not in self._gesture_items:
            self._gesture_items.append(name)
        self.gesture = name
        self._custom_gesture_field.value = ""
        self._rebuild_preset_list()
        self._set_status(f"Preset target → {name}  (Save Preset to store)")
        self._safe_update()

    def _remove_gesture(self, e=None):
        name = self.gesture
        if name in PERMANENT:
            self._set_status(f"'{name}' is permanent — cannot remove"); return
        self._show_confirm(
            f"Delete preset '{name}'?",
            "This only removes it from the list — saved files are not deleted.",
            on_confirm=lambda: self._do_remove_gesture(name))

    def _do_remove_gesture(self, name: str):
        if name in self._gesture_items:
            self._gesture_items.remove(name)
        self.gesture = self._gesture_items[0] if self._gesture_items else ""
        self._rebuild_preset_list()
        self._set_status(f"Removed preset '{name}'")
        self._safe_update()

    def _save_preset(self, e=None, speed: float = 1.0):
        if not self.sequence:
            self._set_status("Add some takes first."); return
        if not self.gesture:
            self._set_status("Select or add a preset name first."); return
        # Presets (incl. the 6 permanent directions) are always OVERWRITABLE, but
        # if one already has a saved animation, confirm before replacing it.
        existing = PRESETS_DIR / self.gesture / "animation.yaml"
        if existing.exists():
            self._show_confirm(
                f"Overwrite preset '{self.gesture}'?",
                f"'{self.gesture}' already has a saved animation.\n"
                f"This replaces it with the current {len(self.sequence)} take(s). "
                f"Cannot be undone.",
                on_confirm=lambda: self._do_save_preset(speed),
                confirm_label="OVERWRITE", confirm_color=ACCENT)
        else:
            self._do_save_preset(speed)

    def _do_save_preset(self, speed: float = 1.0):
        try:
            frames = _even_speed(_compose_sequence([it.take for it in self.sequence]))
            if not frames:
                self._set_status("Nothing to save — record a take first."); return
            sides = set()
            for f in frames:
                sides.update(k for k in f.keys() if k in ("left", "right"))
            if not sides:
                sides = {"left", "right"}
            out_fps = max(1, round(REC_HZ * speed))
            data = {"gesture": self.gesture, "fps": out_fps,
                    "frames": {s: [[float(v) for v in f.get(s, list(HOME_POSE))]
                                   for f in frames] for s in sorted(sides)}}
            dest = PRESETS_DIR / self.gesture
            dest.mkdir(parents=True, exist_ok=True)
            path = dest / "animation.yaml"
            path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
        except Exception as exc:
            self._set_status(f"SAVE FAILED for '{self.gesture}': {exc}")
            return
        if self.gesture not in self._gesture_items:
            self._gesture_items.append(self.gesture)
        self._rebuild_preset_list()          # show the new ✓ saved marker
        note = f" @ ×{speed:.2f} (fps={out_fps})" if speed != 1.0 else ""
        self._set_status(f"✓ Saved '{self.gesture}' → {path}  "
                         f"({len(frames)} fr, {len(frames)/out_fps:.1f}s){note}")

    def _save_at_speed(self, e=None):
        self._save_preset(speed=self.speed_mult)

    def _play_from_final(self, e=None):
        self._play_gesture(reverse=False)

    def _play_from_final_rev(self, e=None):
        self._play_gesture(reverse=True)

    def _play_gesture(self, reverse: bool):
        """Play the selected preset: prefer final_saves, fall back to presets."""
        final  = FINAL_DIR / self.gesture / "animation.yaml"
        preset = PRESETS_DIR / self.gesture / "animation.yaml"
        if final.exists():
            self._play_yaml(final, reverse)
        elif preset.exists():
            self._play_yaml(preset, reverse)
        else:
            self._show_info(
                "No Saved Preset",
                f"'{self.gesture}' has no animation.yaml.\n"
                "Record takes and press Save Preset first.")

    def _play_yaml(self, path: Path, reverse: bool):
        data = yaml.safe_load(path.read_text()) or {}
        fps  = float(data.get("fps", REC_HZ))
        raw  = data.get("frames", {})
        sides = list(raw.keys())
        if not sides:
            self._set_status("Preset has no frames"); return
        n = max(len(raw[s]) for s in sides)
        frames = [{s: raw[s][min(i, len(raw[s]) - 1)] for s in sides} for i in range(n)]
        if reverse:
            frames = list(reversed(frames))
        d = "rev" if reverse else "fwd"
        self._start_segments([frames], fps,
                             f"Playing {self.gesture} ({d}): {n} fr, {n/fps:.1f}s")

    # ------------------------------------------------------------------ #
    #  Recording
    # ------------------------------------------------------------------ #
    def _start_rec(self, e=None):
        if self._recording:
            return
        self._rec_frames = []
        self._rec_start = self._rec_last_t = time.monotonic()
        self._recording = True
        self.rec_btn.disabled = True
        self.stop_btn.disabled = False
        self._set_status("Recording… move the joints you want")

    def _stop_rec(self, e=None):
        if not self._recording:
            return
        self._recording = False
        self.rec_btn.disabled = False
        self.stop_btn.disabled = True
        self.rec_info.value = "0 FRAMES · 0.0s"
        raw = list(self._rec_frames)
        if not raw:
            self._show_info("Empty Take", "Nothing was recorded."); return
        frames = _trim_static(raw)
        if not frames:
            self._show_info("Empty Take", "No movement detected — take discarded."); return

        def _total_motion(fs):
            total = 0.0
            for s in ("left", "right"):
                vals = [f.get(s, _z()) for f in fs]
                for j in range(NJ):
                    col = [v[j] for v in vals]
                    total += max(col) - min(col)
            return total
        if _total_motion(frames) < MOVE_THRESHOLD * 2:
            self._show_info("Empty Take",
                            "Sliders barely moved — take discarded.\n"
                            "Move at least one joint further."); return

        n_raw = len(frames)
        frames = self._apply_smoothing(frames)
        take = Take(frames, REC_HZ)
        self.sequence.append(SeqItem(take))
        self._set_status(f"Saved '{take.name}': {n_raw}→{len(frames)} fr, {take.duration:.1f}s")
        self._rebuild_seq()

    def _apply_smoothing(self, frames):
        """Apply the selected smoothing mode + strength to a frame list."""
        mode, strength = self.smooth_mode, self.smooth_strength
        if not frames or mode == "None":
            return frames
        if mode == "Linear":
            return _smooth_linear(frames, max(0.01, 0.4 - strength * 0.38))
        if mode == "EMA":
            a = max(0.05, 0.9 - strength * 0.85)
            return _smooth_ema(_smooth_ema(frames, a), a)
        return frames

    # ------------------------------------------------------------------ #
    #  Sequence actions
    # ------------------------------------------------------------------ #
    def _play_selected(self, e=None):
        sel = self._selected()
        items = [self.sequence[i] for i in sel] if sel else self.sequence
        if not items:
            self._set_status("Nothing to play."); return
        segs = _compose_segments([it.take for it in items])
        self._start_segments(segs, REC_HZ, f"Playing sequence: {len(segs)} take(s)")

    def _play_selected_rev(self, e=None):
        if not self.sequence:
            self._set_status("Nothing to play."); return
        segs = _compose_segments([it.take for it in self.sequence])
        segs = [list(reversed(s)) for s in reversed(segs)]
        self._start_segments(segs, REC_HZ, f"Playing reverse: {len(segs)} take(s)")

    def _combine_selected(self, e=None):
        sel = self._selected()
        if len(sel) < 2:
            self._set_status("Check 2+ takes to combine."); return
        children = [self.sequence[i].take for i in sel]
        merged = _parallel_merge(children)
        merged.frames = self._apply_smoothing(merged.frames)
        group = SeqItem(merged, children)
        first = sel[0]
        for i in sorted(sel, reverse=True):
            self.sequence.pop(i)
        self.sequence.insert(first, group)
        self._set_status(f"Combined {len(children)} tracks → {len(group.take.frames)} fr")
        self._rebuild_seq()

    def _uncombine_selected(self, e=None):
        sel = self._selected()
        targets = [i for i in sel if self.sequence[i].is_group]
        if not targets:
            self._set_status("Check a Combined group to split."); return
        for i in sorted(targets, reverse=True):
            item = self.sequence[i]
            children = [SeqItem(t) for t in item.children]
            self.sequence.pop(i)
            for j, child in enumerate(children):
                self.sequence.insert(i + j, child)
        self._set_status(f"Uncombined {len(targets)} group(s)")
        self._rebuild_seq()

    def _delete_selected(self, e=None):
        sel = self._selected()
        if not sel:
            return
        for i in sorted(sel, reverse=True):
            self.sequence.pop(i)
        self._set_status(f"Deleted {len(sel)} item(s)")
        self._rebuild_seq()

    def _clear_all(self, e=None):
        if not self.sequence:
            return
        self.sequence.clear()
        Take._counter = 0
        self._set_status("Cleared")
        self._rebuild_seq()

    # ------------------------------------------------------------------ #
    #  Dialogs / status
    # ------------------------------------------------------------------ #
    def _show_info(self, title: str, body: str):
        def _ok(e):
            self.page.pop_dialog()
        dlg = ft.AlertDialog(
            modal=True, bgcolor=CARD2,
            title=ft.Text(title, size=16, weight=ft.FontWeight.W_900, color=FG_VAL),
            content=ft.Text(body, size=13, color=FG_DIM),
            actions=[ft.TextButton("OK", on_click=_ok, style=ft.ButtonStyle(color=ACCENT))],
            actions_alignment=ft.MainAxisAlignment.END)
        self.page.show_dialog(dlg)

    def _show_prompt(self, title: str, body: str, initial: str, on_submit,
                     submit_label: str = "OK"):
        field = ft.TextField(value=initial, dense=True, filled=True, bgcolor=CARD,
                             border_radius=8, border_color=BORDER, text_size=14,
                             color=FG, autofocus=True)
        def _ok(e):
            self.page.pop_dialog(); on_submit(field.value or "")
        def _cancel(e):
            self.page.pop_dialog()
        dlg = ft.AlertDialog(
            modal=True, bgcolor=CARD2,
            title=ft.Text(title, size=16, weight=ft.FontWeight.W_900, color=FG),
            content=ft.Column([ft.Text(body, size=13, color=FG_DIM), field],
                              tight=True, spacing=10),
            actions=[ft.TextButton("CANCEL", on_click=_cancel, style=ft.ButtonStyle(color=FG_DIM)),
                     ft.TextButton(submit_label, on_click=_ok, style=ft.ButtonStyle(color=ACCENT))],
            actions_alignment=ft.MainAxisAlignment.END)
        self.page.show_dialog(dlg)

    def _show_confirm(self, title: str, body: str, on_confirm,
                      confirm_label: str = "DELETE", confirm_color=None):
        confirm_color = confirm_color or DANGER
        def _yes(e):
            self.page.pop_dialog(); on_confirm()
        def _no(e):
            self.page.pop_dialog()
        dlg = ft.AlertDialog(
            modal=True, bgcolor=CARD2,
            title=ft.Text(title, size=16, weight=ft.FontWeight.W_900, color=FG),
            content=ft.Text(body, size=13, color=FG_DIM),
            actions=[ft.TextButton("CANCEL", on_click=_no, style=ft.ButtonStyle(color=FG_DIM)),
                     ft.TextButton(confirm_label, on_click=_yes,
                                   style=ft.ButtonStyle(color=confirm_color))],
            actions_alignment=ft.MainAxisAlignment.END)
        self.page.show_dialog(dlg)

    def _set_status(self, msg: str):
        self.status.value = msg
        self._safe_update()

    def _safe_update(self):
        try:
            self.page.update()
        except Exception:
            pass

    def close(self):
        self._recording = False
        self._playing = False
        self.channel.close()


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="G1 arm trajectory recorder (Flet)")
    ap.add_argument("--iface", default=None,
                    help="NIC connected to the G-1 (default: auto-detect the 192.168.123.x NIC)")
    ap.add_argument("--step", type=float, default=0.02,
                    help="ramp speed in rad per 20 ms tick (default: 0.02)")
    ap.add_argument("--dry-run", action="store_true", help="UI only, no DDS")
    ap.add_argument("--web", action="store_true", help="open in a browser")
    ap.add_argument("--theme", choices=list(THEMES), default="yellow")
    args, _ = ap.parse_known_args()

    _apply_theme(args.theme)
    iface = args.iface if args.iface else _resolve_g1_iface()
    if not args.dry_run:
        print(f"[arm_rec] using DDS interface: {iface or '(none found on 192.168.123.x)'}")

    def target(page: ft.Page):
        app = RecorderApp(page, iface=iface or "", step=args.step, dry_run=args.dry_run)
        page.on_close = lambda e: app.close()

    view = ft.AppView.WEB_BROWSER if args.web else ft.AppView.FLET_APP
    ft.run(target, view=view)


if __name__ == "__main__":
    main()
