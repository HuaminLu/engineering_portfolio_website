#!/usr/bin/env python3
"""arm_gui.py — a clean, arm-only control GUI for the Unitree G-1 (Flet).

This is a from-scratch, *arms-only* alternative to ``run_geoff_gui.py``.  It
drops all of the RealSense / SLAM / point-cloud / Dex3-hand machinery and keeps
just what you need to move the arms:

* **Both arms are live at once.**  A dedicated key cluster drives each side —
  no mode switch:

      LEFT  arm :  R T Y            RIGHT arm :  U I O
                   F G H                         J K L

      T / G = up / down       I / K = up / down
      F / H = left / right     J / L = left / right
      R / Y = forward / back   U / O = forward / back

* **Tele-op keys (W A S D Q E) are reserved** in the legend but intentionally
  *not* implemented here — this GUI is arm-only.
* **Damp arms & center waist** puts both arms in passive (back-drivable) mode
  and squares the torso, exactly like the button in ``run_geoff_gui.py``.

Motion uses the same averaged-delta model as the main GUI
(:func:`data.inference_arm.predict_delta_target`): each keypress nudges the arm
from its *current* measured pose by the mean joint-space displacement recorded
for that direction, ramped smoothly at ``--step`` rad / 20 ms tick.

Only the **left** arm has a trained model today (100 recorded samples).  Right
keys fall back to the left bundle applied to joints 22–28 (approximate, not
kinematically mirrored) until ``data/artifacts/right-arm/arm_deltas.joblib``
exists, at which point the real model is picked up automatically.

    python3 arm_gui.py --iface enxa0cec8b8657b        # real robot
    python3 arm_gui.py --iface lo                      # UI smoke-test, no robot
    python3 arm_gui.py --iface enxa0cec8b8657b --web   # browser instead of native

Only run ONE ``rt/arm_sdk`` publisher at a time — do not launch this alongside
``run_geoff_gui.py`` or ``arm_train_recorder.py``.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Joint layout (Unitree G-1, unitree_hg LowCmd motor indices)
# --------------------------------------------------------------------------- #
WAIST_YAW_IDX = 12
LEFT_IDX = list(range(15, 22))   # 15..21
RIGHT_IDX = list(range(22, 29))  # 22..28
NOT_USED_IDX = 29                # motor_cmd[29].q = 1 enables arm_sdk control
ARM_INDICES = LEFT_IDX + RIGHT_IDX

# Generic 7-DoF names (ascending motor index) — good enough for a readout.
JOINT_NAMES = ["Sh.Pitch", "Sh.Roll", "Sh.Yaw", "Elbow", "Wr.Roll", "Wr.Pitch", "Wr.Yaw"]

# Comfortable standby ("ready") poses, copied from run_geoff_gui.py.
READY_POSE = {
    "left": {15: 0.211, 16: 0.181, 17: -0.284, 18: 0.672, 19: -0.379, 20: -0.852, 21: -0.019},
    "right": {22: 0.087, 23: -0.271, 24: 0.323, 25: 0.691, 26: 0.240, 27: -0.771, 28: -0.176},
}

# key -> direction, per side.
LEFT_KEYS = {"t": "up", "g": "down", "f": "left", "h": "right", "r": "forward", "y": "back"}
RIGHT_KEYS = {"i": "up", "k": "down", "j": "left", "l": "right", "u": "forward", "o": "back"}
TELEOP_KEYS = set("wasdqe")

# Stiff / limp gains.
KP_STIFF, KD_STIFF = 60.0, 1.5

# Per-joint (min, max) radian limits from g1_29dof.xml (only shoulder-roll differs
# L/R).  Used to clamp trajectory-playback targets so a queued frame can never
# drive a joint out of range.
_LIMITS_LEFT = [
    (-3.089, 2.670), (-1.588, 2.252), (-2.618, 2.618), (-1.047, 2.094),
    (-1.972, 1.972), (-1.614, 1.614), (-1.614, 1.614),
]
_LIMITS_RIGHT = [
    (-3.089, 2.670), (-2.252, 1.588), (-2.618, 2.618), (-1.047, 2.094),
    (-1.972, 1.972), (-1.614, 1.614), (-1.614, 1.614),
]


def _clamp_arm(arm: str, k: int, v: float) -> float:
    lo, hi = (_LIMITS_LEFT if arm == "left" else _LIMITS_RIGHT)[k]
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
#  Controller: DDS + motion logic (no Flet imports live here)
# --------------------------------------------------------------------------- #
class ArmController:
    """Owns the ``rt/arm_sdk`` publisher and a 50 Hz ramp loop.

    All shared state is guarded by ``self._lock``.  The Flet UI thread only ever
    calls the public methods (:meth:`nudge`, :meth:`damp_center`, …) and reads
    :meth:`snapshot`; the DDS publish loop runs in its own daemon thread so the
    UI never blocks on the middleware.
    """

    def __init__(self, iface: str, gain: float = 1.0, step: float = 0.008):
        self.iface = iface
        self.gain = gain
        self.step = step

        self._lock = threading.Lock()
        self.cmd_q: dict[int, float] = {i: 0.0 for i in (WAIST_YAW_IDX, *ARM_INDICES)}
        self.target: dict[int, float] = dict(self.cmd_q)
        self.joint_cur: dict[int, float] = {}
        self.damped = {"left": False, "right": False}
        self._initialised_from_state = False
        self._touched: set[int] = set()   # joints the user has commanded

        self.connected = False          # became True once a LowState packet arrived
        self.sdk_ok = False             # publisher created successfully
        self.last_status = "starting…"

        # model bundles per side (lazy) + a note about fallback
        self._bundle_cache: dict[str, object] = {}
        self.model_note = {"left": "not loaded", "right": "not loaded"}

        # trajectory bundles (preferred over the averaged-delta model when present)
        self._traj_cache: dict[str, object] = {}
        # per-arm playback queue of absolute target frames (chase model)
        self._play_queue: dict[str, list[list[float]]] = {"left": [], "right": []}
        self._play_cursor: dict[str, int] = {"left": 0, "right": 0}

        self._pub = None
        self._cmd = None
        self._crc = None

        self._init_dds()

    # ---- DDS bring-up ---------------------------------------------------- #
    def _init_dds(self) -> None:
        try:
            from unitree_sdk2py.core.channel import (  # type: ignore
                ChannelFactoryInitialize,
                ChannelPublisher,
            )
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_  # type: ignore
            from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_  # type: ignore
            from unitree_sdk2py.utils.crc import CRC  # type: ignore
        except Exception as exc:  # pragma: no cover
            self.last_status = f"SDK import failed: {exc}"
            print(f"[arm_gui] {self.last_status}", file=sys.stderr)
            return

        try:
            ChannelFactoryInitialize(0, self.iface)
            self._cmd = unitree_hg_msg_dds__LowCmd_()
            self._crc = CRC()
            self._cmd.motor_cmd[NOT_USED_IDX].q = 1.0  # enable arm_sdk
            self._pub = ChannelPublisher("rt/arm_sdk", LowCmd_)
            self._pub.Init()
            self.sdk_ok = True
            self.last_status = f"arm_sdk publisher ready on {self.iface}"
            print(f"[arm_gui] {self.last_status}")
        except Exception as exc:  # pragma: no cover
            self.last_status = f"arm_sdk init failed: {exc}"
            print(f"[arm_gui] {self.last_status}", file=sys.stderr)
            return

        self._start_lowstate_sub()

    def _start_lowstate_sub(self) -> None:
        """Subscribe to rt/lowstate in a daemon thread (HG then GO namespace)."""

        def _run():
            from unitree_sdk2py.core.channel import ChannelSubscriber  # type: ignore

            candidates = [
                "unitree_sdk2py.idl.unitree_hg.msg.dds_.LowState_",
                "unitree_sdk2py.idl.unitree_go.msg.dds_.LowState_",
            ]
            for dotted in candidates:
                try:
                    mod_path, cls = dotted.rsplit(".", 1)
                    mod = __import__(mod_path, fromlist=[cls])
                    LowState_ = getattr(mod, cls)

                    def _cb(msg):
                        with self._lock:
                            for j in (WAIST_YAW_IDX, *ARM_INDICES):
                                try:
                                    self.joint_cur[j] = msg.motor_state[j].q
                                except Exception:
                                    pass
                            self.connected = True
                    sub = ChannelSubscriber("rt/lowstate", LowState_)
                    sub.Init(_cb, 200)
                    self._ls_sub = sub  # keep ref alive
                    return
                except Exception:
                    continue

        threading.Thread(target=_run, daemon=True).start()

    def start(self) -> None:
        """Launch the 50 Hz publish loop (idempotent)."""
        if not self.sdk_ok:
            return
        threading.Thread(target=self._publish_loop, daemon=True).start()

    # ---- 50 Hz publisher ------------------------------------------------- #
    def _publish_loop(self) -> None:
        while True:
            time.sleep(0.02)
            try:
                self._tick_once()
            except Exception as exc:  # never let the loop die
                print(f"[arm_gui] publish error: {exc}", file=sys.stderr)

    def _tick_once(self) -> None:
        with self._lock:
            # One-shot snap-free init from the first measured sample: seed the
            # ramp origin (cmd_q) to where the arm actually is.  Only seed the
            # *target* for joints the user hasn't already commanded, so an early
            # keypress that lands before the first LowState packet isn't wiped.
            if not self._initialised_from_state and self.joint_cur:
                for j, q in self.joint_cur.items():
                    if j in self.cmd_q:
                        self.cmd_q[j] = q
                        if j != WAIST_YAW_IDX and j not in self._touched:
                            self.target[j] = q  # waist target stays 0 (centred)
                self._initialised_from_state = True

            # Trajectory playback (chase model): for each side with a queued
            # trajectory, point target at the current frame; only advance to the
            # next frame once cmd_q has reached this one — so every joint passes
            # through every recorded in-between frame (never skips).
            for side, idxs in (("left", LEFT_IDX), ("right", RIGHT_IDX)):
                queue = self._play_queue[side]
                if not queue:
                    continue
                cur_idx = min(self._play_cursor[side], len(queue) - 1)
                frame = queue[cur_idx]
                for k, j in enumerate(idxs):
                    self.target[j] = frame[k]
                reached = all(
                    abs(self.cmd_q.get(j, 0.0) - frame[k]) <= 0.02
                    for k, j in enumerate(idxs)
                )
                if reached:
                    if cur_idx >= len(queue) - 1:
                        self._play_queue[side] = []   # done; hold last frame
                    else:
                        self._play_cursor[side] = cur_idx + 1

            # Ramp every commanded joint toward its target.
            for j, tgt in self.target.items():
                cur = self.cmd_q.get(j, 0.0)
                diff = tgt - cur
                if abs(diff) <= 0.01:
                    self.cmd_q[j] = tgt
                else:
                    stp = self.step if diff > 0 else -self.step
                    if abs(stp) > abs(diff):
                        stp = diff
                    self.cmd_q[j] = cur + stp

            damped = dict(self.damped)
            cmd_q = dict(self.cmd_q)

        if self._pub is None:
            return

        # waist: always stiff, held at 0 (centred torso)
        w = self._cmd.motor_cmd[WAIST_YAW_IDX]
        w.q, w.dq, w.tau, w.kp, w.kd = 0.0, 0.0, 0.0, KP_STIFF, KD_STIFF

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

    # ---- model loading --------------------------------------------------- #
    def _get_bundle(self, arm: str):
        """Return (bundle, effective_arm).  Right falls back to the left bundle."""
        if arm in self._bundle_cache:
            return self._bundle_cache[arm], self.model_note[arm]

        from data.inference_arm import load_deltas  # type: ignore

        try:
            bundle = load_deltas(
                Path(f"data/artifacts/{arm}-arm/arm_deltas.joblib"), arm=arm
            )
            self.model_note[arm] = "model ✓"
        except Exception:
            if arm == "right":
                # Fall back to the left model applied to the right joints.
                try:
                    bundle = load_deltas(
                        Path("data/artifacts/left-arm/arm_deltas.joblib"), arm="left"
                    )
                    self.model_note[arm] = "LEFT model (approx)"
                except Exception:
                    self.model_note[arm] = "no model"
                    return None, self.model_note[arm]
            else:
                self.model_note[arm] = "no model"
                return None, self.model_note[arm]

        self._bundle_cache[arm] = bundle
        return bundle, self.model_note[arm]

    def _get_traj(self, arm: str):
        """Return a trajectory bundle for *arm*, or None if none is built yet."""
        if arm in self._traj_cache:
            return self._traj_cache[arm]
        from data.inference_arm import load_traj  # type: ignore
        try:
            bundle = load_traj(Path(f"data/artifacts/{arm}-arm/arm_traj.joblib"), arm=arm)
        except Exception:
            self._traj_cache[arm] = None
            return None
        self._traj_cache[arm] = bundle
        return bundle

    # ---- public actions -------------------------------------------------- #
    def nudge(self, arm: str, direction: str) -> str:
        """Move *arm* in *direction*.

        Prefers the recorded **trajectory** (replays every joint's crafted path
        from the current pose); falls back to the averaged-delta model when no
        trajectory bundle exists for this arm/direction.
        """
        if not self.sdk_ok:
            return "arm_sdk unavailable — cannot move"

        idxs = LEFT_IDX if arm == "left" else RIGHT_IDX

        # ---- trajectory playback (preferred) ----
        traj = self._get_traj(arm)
        if traj is not None:
            from data.inference_arm import predict_delta_trajectory  # type: ignore
            deltas = predict_delta_trajectory(direction, traj)
            if deltas:
                with self._lock:
                    current = [self.joint_cur.get(j, self.cmd_q.get(j, 0.0)) for j in idxs]
                    queue = [[_clamp_arm(arm, k, current[k] + d[k]) for k in range(len(idxs))]
                             for d in deltas]
                    self.damped[arm] = False
                    self._play_queue[arm] = queue
                    self._play_cursor[arm] = 0
                    for j in idxs:
                        self._touched.add(j)
                self.model_note[arm] = "traj ✓"
                return f"{arm} arm → {direction} (traj, {len(queue)} fr)"

        # ---- averaged-delta fallback ----
        from data.inference_arm import predict_delta_target  # type: ignore
        bundle, note = self._get_bundle(arm)
        if bundle is None:
            return f"{arm} arm: {note} — record data first"

        with self._lock:
            self._play_queue[arm] = []   # cancel any active trajectory
            current = [self.joint_cur.get(j, self.cmd_q.get(j, 0.0)) for j in idxs]

        eff_arm = "left" if note.startswith("LEFT") else arm
        target = predict_delta_target(
            direction, current, gain=self.gain, bundle=bundle, arm=eff_arm
        )

        with self._lock:
            self.damped[arm] = False  # taking control resumes stiffness
            for j, q in zip(idxs, target):
                self.target[j] = float(q)
                self._touched.add(j)

        return f"{arm} arm → {direction} ({note})"

    def ready_pose(self, arm: str) -> str:
        with self._lock:
            self._play_queue[arm] = []   # cancel any active trajectory
            self.damped[arm] = False
            for j, q in READY_POSE[arm].items():
                self.target[j] = q
                self._touched.add(j)
        return f"{arm} arm → ready pose"

    def damp_center(self, side: str = "both") -> str:
        sides = ("left", "right") if side == "both" else (side,)
        with self._lock:
            for s in sides:
                self._play_queue[s] = []   # cancel any active trajectory
                self.damped[s] = True
                # hold current so there's no jump when stiffness drops
                for j in (LEFT_IDX if s == "left" else RIGHT_IDX):
                    self.target[j] = self.cmd_q.get(j, 0.0)
            self.target[WAIST_YAW_IDX] = 0.0
        which = "both arms" if side == "both" else f"{side} arm"
        return f"damped {which}, waist centred"

    def center_waist(self) -> str:
        with self._lock:
            self.target[WAIST_YAW_IDX] = 0.0
        return "waist centred (0 rad)"

    def resume(self) -> str:
        with self._lock:
            self.damped = {"left": False, "right": False}
        return "resumed stiff hold on both arms"

    # ---- read-only view for the UI --------------------------------------- #
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "connected": self.connected,
                "sdk_ok": self.sdk_ok,
                "damped": dict(self.damped),
                "cur": dict(self.joint_cur),
                "cmd": dict(self.cmd_q),
                "model_note": dict(self.model_note),
            }


# --------------------------------------------------------------------------- #
#  Flet UI
# --------------------------------------------------------------------------- #
BG = "#1e1e1e"
CARD = "#252525"
FG = "#e6e6e6"
YELLOW = "#f4c430"
GREEN = "#5fd35f"
RED = "#e05a5a"
DIM = "#7a7a7a"
BORDER = "#3a3a3a"
MONO = "monospace"


def _border_all(width: float, color: str):
    import flet as ft
    side = ft.BorderSide(width=width, color=color)
    return ft.Border(top=side, right=side, bottom=side, left=side)


def build_ui(page, ctrl: ArmController, iface: str) -> None:
    import asyncio
    import flet as ft

    ORANGE = "#e8873a"

    page.title = "G1 Arm Control"
    page.bgcolor = BG
    page.padding = ft.Padding(left=14, top=14, right=14, bottom=14)
    try:
        page.window.width = 1200
        page.window.height = 900
    except Exception:
        pass

    # ---- focus state (declared early; set_focus defined after cards) -------
    focus = {"arm": "left"}
    cards: dict[str, ft.Container] = {}
    tab_containers: dict[str, ft.Container] = {}

    # ---- header ------------------------------------------------------------
    title = ft.Text("G1 Arm Control", size=22, weight=ft.FontWeight.BOLD, color=FG)
    conn_pill = ft.Container(
        content=ft.Text("waiting for robot…", size=12, color="#1e1e1e",
                        weight=ft.FontWeight.BOLD),
        bgcolor=RED, padding=ft.Padding(left=14, top=5, right=14, bottom=5),
        border_radius=14,
    )
    header = ft.Row([title, conn_pill], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

    # ---- full-width tab selector -------------------------------------------
    def _make_tab(arm: str, label: str) -> ft.Container:
        txt = ft.Text(label, size=13, weight=ft.FontWeight.BOLD,
                      text_align=ft.TextAlign.CENTER, color=DIM)
        c = ft.Container(
            content=txt, expand=True, height=42,
            bgcolor="#2d2d2d", border_radius=7,
            alignment=ft.Alignment.CENTER,
            on_click=lambda e, a=arm: set_focus(a),
        )
        tab_containers[arm] = c
        return c

    tabs_row = ft.Row([
        _make_tab("left",  "LEFT ARM  ·  T G F H R Y"),
        ft.Container(width=8),
        _make_tab("right", "RIGHT ARM  ·  I K J L U O"),
    ], spacing=0)

    # ---- per-arm cards (expand to fill width) ------------------------------
    readouts: dict[str, list] = {"left": [], "right": []}
    chips: dict[str, dict[str, ft.Container]] = {"left": {}, "right": {}}
    model_chip: dict[str, ft.Text] = {}
    chip_flash: dict[tuple, float] = {}

    def make_card(arm: str, keys: dict[str, str]) -> ft.Container:
        idxs = LEFT_IDX if arm == "left" else RIGHT_IDX
        rows = []
        for name, j in zip(JOINT_NAMES, idxs):
            val = ft.Text("  0.000", font_family=MONO, size=13, color=FG)
            readouts[arm].append(val)
            rows.append(
                ft.Row([
                    ft.Text(f"{name:<9}", font_family=MONO, size=13, color=DIM),
                    ft.Text(f"[{j}]", font_family=MONO, size=11, color=DIM),
                    val,
                ], spacing=8)
            )

        mchip = ft.Text("model: …", size=12, color=YELLOW, weight=ft.FontWeight.BOLD)
        model_chip[arm] = mchip

        def chip(k: str) -> ft.Container:
            c = ft.Container(
                content=ft.Text(f"{k.upper()}\n{keys[k]}", size=11, color=FG,
                                text_align=ft.TextAlign.CENTER),
                expand=True, height=44, bgcolor="#2d2d2d", border_radius=6,
                border=_border_all(1, BORDER), alignment=ft.Alignment.CENTER,
            )
            chips[arm][k] = c
            return c

        by_dir = {v: k for k, v in keys.items()}
        grid = ft.Column([
            ft.Row([chip(by_dir["forward"]), chip(by_dir["up"]), chip(by_dir["back"])],
                   spacing=6),
            ft.Row([chip(by_dir["left"]), chip(by_dir["down"]), chip(by_dir["right"])],
                   spacing=6),
        ], spacing=6)

        title_row = ft.Row(
            [ft.Text(f"{arm.upper()} ARM", size=16, weight=ft.FontWeight.BOLD, color=FG),
             mchip],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        body = ft.Column(
            [title_row, ft.Divider(color=BORDER, height=1), *rows,
             ft.Container(height=4), grid],
            spacing=5,
        )
        card = ft.Container(
            content=body, bgcolor=CARD, padding=16, border_radius=10,
            border=_border_all(1, BORDER), expand=True,
        )
        cards[arm] = card
        return card

    card_left = make_card("left", LEFT_KEYS)
    card_right = make_card("right", RIGHT_KEYS)
    cards_row = ft.Row(
        [card_left, card_right], spacing=12, expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    # ---- status line -------------------------------------------------------
    status = ft.Text(ctrl.last_status, size=12, color=YELLOW, font_family=MONO,
                     expand=True, no_wrap=True)

    def set_status(msg: str) -> None:
        status.value = msg
        page.update()

    # ---- control buttons ---------------------------------------------------
    def on_damp(e):   set_status(ctrl.damp_center("both"))
    def on_center(e): set_status(ctrl.center_waist())
    def on_ready(e):  set_status(ctrl.ready_pose(focus["arm"]))
    def on_resume(e): set_status(ctrl.resume())

    btn_damp   = ft.Button("Damp arms & center waist", bgcolor="#5d2f2f",
                           color=FG, on_click=on_damp)
    btn_center = ft.Button("Center waist", bgcolor="#2d2d2d",
                           color=FG, on_click=on_center)
    btn_ready  = ft.Button("Ready pose (focused arm)", bgcolor="#2f5d2f",
                           color=FG, on_click=on_ready)
    btn_resume = ft.Button("Resume hold", bgcolor="#2d2d2d",
                           color=FG, on_click=on_resume)
    buttons = ft.Row([btn_damp, btn_center, btn_ready, btn_resume],
                     spacing=8, wrap=True)

    # ---- tuning sliders ----------------------------------------------------
    gain_val = ft.Text(f"{ctrl.gain:.2f}", font_family=MONO, color=FG, width=52)
    step_val = ft.Text(f"{ctrl.step:.3f}", font_family=MONO, color=FG, width=52)

    def on_gain(e):
        ctrl.gain = float(e.control.value)
        gain_val.value = f"{ctrl.gain:.2f}"
        page.update()

    def on_step(e):
        ctrl.step = float(e.control.value)
        step_val.value = f"{ctrl.step:.3f}"
        page.update()

    gain_slider = ft.Slider(min=0.0, max=2.0, value=ctrl.gain, divisions=40,
                            on_change=on_gain, active_color=YELLOW, expand=True)
    step_slider = ft.Slider(min=0.002, max=0.5, value=ctrl.step, divisions=100,
                            on_change=on_step, active_color=YELLOW, expand=True)

    def _zone_bar(zones):
        cells = []
        for label, flex, bg, fg in zones:
            cells.append(
                ft.Container(
                    content=ft.Text(label, size=10, color=fg, font_family=MONO,
                                    text_align=ft.TextAlign.CENTER, no_wrap=True),
                    bgcolor=bg, expand=flex,
                    padding=ft.Padding(left=4, top=2, right=4, bottom=2),
                    border_radius=4,
                )
            )
        return ft.Row(cells, spacing=3, expand=True)

    gain_zones = _zone_bar([
        ("subtle  0 – 0.5",          25, "#222222", DIM),
        ("normal  0.5 – 1.5",        50, "#1d2b1d", GREEN),
        ("aggressive  1.5 – 2.0  >>", 25, "#2b1e0e", ORANGE),
    ])
    step_zones = _zone_bar([
        ("smooth  0 – 0.05",         25, "#222222", DIM),
        ("ramp  0.05 – 0.35",        40, "#1d2b1d", GREEN),
        ("instant  0.35 – 0.5  >>",  35, "#2b1e0e", ORANGE),
    ])

    LBL_W = 52

    def _slider_block(label, slider, val_text, zones):
        return ft.Column([
            ft.Row([
                ft.Text(label, color=DIM, width=LBL_W, font_family=MONO, size=13),
                slider,
                val_text,
            ], spacing=8),
            ft.Row([
                ft.Container(width=LBL_W),
                zones,
                ft.Container(width=LBL_W),
            ], spacing=0),
        ], spacing=3)

    # ---- keymap legend (compact, two-column) -------------------------------
    legend_row = ft.Row([
        ft.Column([
            ft.Text("LEFT arm", size=12, weight=ft.FontWeight.BOLD, color=GREEN),
            ft.Text("T/G up/dn  ·  F/H left/rt  ·  R/Y fwd/bk",
                    font_family=MONO, size=11, color=GREEN),
        ], spacing=2, expand=True),
        ft.Container(width=20),
        ft.Column([
            ft.Text("RIGHT arm", size=12, weight=ft.FontWeight.BOLD, color=GREEN),
            ft.Text("I/K up/dn  ·  J/L left/rt  ·  U/O fwd/bk",
                    font_family=MONO, size=11, color=GREEN),
        ], spacing=2, expand=True),
        ft.Container(width=20),
        ft.Column([
            ft.Text("Teleop (reserved)", size=11, color=DIM),
            ft.Text("W A S D Q E", font_family=MONO, size=11, color=DIM),
        ], spacing=2),
    ])

    # ---- bottom floating card ----------------------------------------------
    bottom_card = ft.Container(
        content=ft.Column([
            ft.Row([
                ft.Text("Controls", size=15, weight=ft.FontWeight.BOLD, color=FG),
                ft.Container(expand=True),
                status,
            ]),
            ft.Divider(color=BORDER, height=1),
            buttons,
            _slider_block("gain", gain_slider, gain_val, gain_zones),
            _slider_block("step", step_slider, step_val, step_zones),
            ft.Divider(color=BORDER, height=1),
            legend_row,
        ], spacing=10),
        bgcolor=CARD, padding=16, border_radius=10,
        border=_border_all(1, BORDER),
        expand=True,
    )

    # ---- focus handling ----------------------------------------------------
    def set_focus(arm: str) -> None:
        focus["arm"] = arm
        for a in ("left", "right"):
            active = (a == arm)
            cards[a].border = _border_all(2 if active else 1,
                                          YELLOW if active else BORDER)
            tc = tab_containers[a]
            tc.bgcolor = YELLOW if active else "#2d2d2d"
            tc.content.color = "#1e1e1e" if active else DIM
        page.update()

    # ---- keyboard ----------------------------------------------------------
    def on_key(e) -> None:
        k = (e.key or "").lower()
        if len(k) != 1:
            return
        if k in LEFT_KEYS:
            set_focus("left")
            chip_flash[("left", k)] = time.time() + 0.25
            set_status(ctrl.nudge("left", LEFT_KEYS[k]))
        elif k in RIGHT_KEYS:
            set_focus("right")
            chip_flash[("right", k)] = time.time() + 0.25
            set_status(ctrl.nudge("right", RIGHT_KEYS[k]))
        elif k in TELEOP_KEYS:
            set_status("tele-op (W/A/S/D/Q/E) reserved — not implemented in this GUI")

    page.on_keyboard_event = on_key

    # ---- main layout -------------------------------------------------------
    # Top section (header + tabs + arm cards) and bottom card each expand=True
    # so they share the available height 50/50 and stretch with window resize.
    top_section = ft.Column([
        header,
        ft.Container(height=6),
        tabs_row,
        ft.Container(height=6),
        cards_row,
    ], spacing=0, expand=True)

    page.add(
        ft.Column([top_section, bottom_card], spacing=12, expand=True)
    )
    set_focus("left")

    # ---- refresh loop (async, ~10 Hz) --------------------------------------
    async def refresh_loop():
        while True:
            snap = ctrl.snapshot()
            if snap["connected"]:
                conn_pill.content.value = f"connected  {iface}"
                conn_pill.bgcolor = GREEN
            elif snap["sdk_ok"]:
                conn_pill.content.value = f"no feedback  {iface}"
                conn_pill.bgcolor = YELLOW
            else:
                conn_pill.content.value = "arm_sdk unavailable"
                conn_pill.bgcolor = RED

            for arm, idxs in (("left", LEFT_IDX), ("right", RIGHT_IDX)):
                for ctl, j in zip(readouts[arm], idxs):
                    v = snap["cur"].get(j, snap["cmd"].get(j, 0.0))
                    ctl.value = f"{v:+7.3f}"
                    ctl.color = DIM if snap["damped"][arm] else FG
                note = snap["model_note"][arm]
                dmp = "  ·  DAMPED" if snap["damped"][arm] else ""
                model_chip[arm].value = f"{note}{dmp}"
                model_chip[arm].color = RED if snap["damped"][arm] else YELLOW

            now = time.time()
            for (arm, k), exp in list(chip_flash.items()):
                c = chips[arm].get(k)
                if c is None:
                    continue
                if now < exp:
                    c.bgcolor = YELLOW
                    c.content.color = "#1e1e1e"
                else:
                    c.bgcolor = "#2d2d2d"
                    c.content.color = FG
                    chip_flash.pop((arm, k), None)

            page.update()
            await asyncio.sleep(0.1)

    page.run_task(refresh_loop)


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="Arm-only Flet control GUI for the Unitree G-1")
    ap.add_argument("--iface", default="enxa0cec8b8657b",
                    help="NIC connected to the G-1 (default: enxa0cec8b8657b)")
    ap.add_argument("--gain", type=float, default=1.0,
                    help="averaged-delta gain per keypress (default: 1.0)")
    ap.add_argument("--step", type=float, default=0.008,
                    help="ramp speed in rad per 20 ms tick (default: 0.008)")
    ap.add_argument("--web", action="store_true",
                    help="open in a web browser instead of a native window")
    args = ap.parse_args()

    import flet as ft

    ctrl = ArmController(args.iface, gain=args.gain, step=args.step)
    ctrl.start()

    def target(page):
        build_ui(page, ctrl, args.iface)

    view = ft.AppView.WEB_BROWSER if args.web else ft.AppView.FLET_APP
    ft.run(target, view=view)


if __name__ == "__main__":
    main()
