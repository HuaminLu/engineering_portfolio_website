#!/usr/bin/env python3
"""arm_train_recorder.py — Sentdex-style ArmTrain data recorder for the G-1.

Records supervised training samples for the arm-direction MLP:

    input  = (direction command, start joint angles)
    output = (end joint angles)

Workflow (matches the video)
============================
1.  Put the robot in **damp mode** so the selected arm is loose and you can
    move it by hand.  (You manage the robot's FSM state yourself.)
2.  Pick the arm (left / right) and how many samples to record.
3.  Hit **Start** — a 3-2-1 countdown plays, then for every sample the app:
      • snapshots the current joint angles         → *start* pose
      • speaks + shows a random direction           ("up", "down", …)
      • gives you N seconds to move the arm that way
      • snapshots the new joint angles              → *end* pose
      • appends one row to the CSV and updates the per-direction stats.
4.  Repeat until you have ~50 samples per direction, then train.

Output
======
    data/arms/<arm>/training_data_with_waist.csv

Columns:
    direction, session_id, quality,
    start_<arm joints…>, end_<arm joints…>.

``session_id`` is a timestamp shared by every sample in one recording run so
you can filter/exclude bad sessions at train time without deleting rows.
``quality`` is a 0-1 score auto-computed from how far the arm moved and how
steady the end pose was; you can override it live by pressing 1-5 during the
brief rating flash after each sample.  The file always **appends** — each
session is bracketed by ``# session …`` comment rows so takes stay legible,
and training scripts skip any line starting with ``#``.

Live joint feedback comes from the DDS ``rt/lowstate`` topic, same as
run_geoff_gui.  Run this on the workstation with the robot on the ethernet
link:

    python3 arm_train_recorder.py --iface enxa0cec8b8657b
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Joint layout (identical to run_geoff_gui / g1_arm_policy_controller)
# ---------------------------------------------------------------------------

LEFT = [
    (15, "L Shoulder Pitch"),
    (16, "L Shoulder Roll"),
    (17, "L Shoulder Yaw"),
    (18, "L Elbow"),
    (19, "L Wrist Roll"),
    (20, "L Wrist Pitch"),
    (21, "L Wrist Yaw"),
]
RIGHT = [
    (22, "R Shoulder Pitch"),
    (23, "R Shoulder Roll"),
    (24, "R Shoulder Yaw"),
    (25, "R Elbow"),
    (26, "R Wrist Roll"),
    (27, "R Wrist Pitch"),
    (28, "R Wrist Yaw"),
]
# Direction commands.  Keys are the canonical CSV labels; values are how they
# are spoken and drawn.
DIRECTIONS = ["forward", "back", "left", "right", "up", "down"]
SPEAK = {
    "forward": "forward",
    "back": "backward",
    "left": "left",
    "right": "right",
    "up": "up",
    "down": "down",
}
GLYPH = {
    "forward": "⬆ FORWARD",   # points away
    "back": "⬇ BACK",
    "left": "⬅ LEFT",
    "right": "➡ RIGHT",
    "up": "↑ UP",
    "down": "↓ DOWN",
}

# ---------------------------------------------------------------------------
# Quality scoring tunables
# ---------------------------------------------------------------------------
# quality = W_DISP * displacement_score + W_STAB * stability_score
#   displacement_score = min(1, L2(end-start arm joints) / QUAL_DISP_FULL)
#   stability_score    = max(0, 1 - mean_std_of_end_window / QUAL_STAB_ZERO)
QUAL_DISP_FULL = 0.40    # rad L2 at which a move earns full displacement credit
QUAL_STAB_ZERO = 0.08    # rad std at which stability credit hits zero
QUAL_STAB_SAMPLES = 16   # ~last 0.5 s of window (30 ms ticks) used for stability
W_DISP = 0.6
W_STAB = 0.4
RATING_SECS = 1.5        # how long the score flashes / accepts a 1-5 override

# ---------------------------------------------------------------------------
# Text-to-speech (non-blocking, best effort)
# ---------------------------------------------------------------------------


def speak(text: str) -> None:
    """Speak ``text`` asynchronously via spd-say (falls back silently)."""
    for cmd in (["spd-say", "-t", "female1", text], ["espeak-ng", text], ["espeak", text]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except FileNotFoundError:
            continue


# ---------------------------------------------------------------------------
# Qt import
# ---------------------------------------------------------------------------

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402


# ---------------------------------------------------------------------------
# DDS LowState reader
# ---------------------------------------------------------------------------


class LowStateReader:
    """Subscribes to rt/lowstate and keeps the latest joint angles."""

    def __init__(self, iface: str, domain: int = 0):
        self.joint_cur: dict[int, float] = {}
        self.msg_count = 0
        self.ok = False
        self._sub = None

        try:
            from unitree_sdk2py.core.channel import (  # type: ignore
                ChannelFactoryInitialize,
                ChannelSubscriber,
            )
        except Exception as exc:  # pragma: no cover
            print(f"[recorder] unitree_sdk2py unavailable: {exc}", file=sys.stderr)
            return

        try:
            ChannelFactoryInitialize(domain, iface)
        except Exception as exc:
            print(f"[recorder] DDS init failed ({exc})", file=sys.stderr)
            return

        candidates = [
            "unitree_sdk2py.idl.unitree_hg.msg.dds_.LowState_",
            "unitree_sdk2py.idl.unitree_go.msg.dds_.LowState_",
        ]
        all_idx = [i for i, _ in LEFT + RIGHT]

        def _cb(msg):
            self.msg_count += 1
            for j in all_idx:
                try:
                    self.joint_cur[j] = float(msg.motor_state[j].q)
                except Exception:
                    pass

        for dotted in candidates:
            try:
                modp, cls = dotted.rsplit(".", 1)
                mod = __import__(modp, fromlist=[cls])
                LowState_ = getattr(mod, cls)
                sub = ChannelSubscriber("rt/lowstate", LowState_)
                sub.Init(_cb, 10)
                self._sub = sub
                self.ok = True
                print(f"[recorder] subscribed to rt/lowstate via {cls}")
                return
            except Exception:
                continue

        print("[recorder] could not subscribe to rt/lowstate", file=sys.stderr)

    def snapshot(self, indices: list[int]) -> list[float]:
        return [self.joint_cur.get(i, 0.0) for i in indices]


# ---------------------------------------------------------------------------
# Waist-lock / arm-free publisher
# ---------------------------------------------------------------------------

ARM_INDICES = [i for i, _ in LEFT + RIGHT]  # 15-21, 22-28
WAIST_YAW_IDX = 12


class WaistLocker:
    """Continuously publishes rt/arm_sdk: waist stiff at 0, both arms zero-torque."""

    def __init__(self):
        self.ok = False
        self._pub = None
        self._cmd = None
        self._crc = None
        try:
            from unitree_sdk2py.core.channel import ChannelPublisher  # type: ignore
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_  # type: ignore
            from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_  # type: ignore
            from unitree_sdk2py.utils.crc import CRC  # type: ignore
        except Exception as exc:
            print(f"[recorder] waist-locker unavailable: {exc}", file=sys.stderr)
            return
        try:
            self._pub = ChannelPublisher("rt/arm_sdk", LowCmd_)
            self._pub.Init()
            self._cmd = unitree_hg_msg_dds__LowCmd_()
            self._crc = CRC()
            # enable arm_sdk
            self._cmd.motor_cmd[29].q = 1.0
            # waist: stiff at 0
            w = self._cmd.motor_cmd[WAIST_YAW_IDX]
            w.q, w.dq, w.tau, w.kp, w.kd = 0.0, 0.0, 0.0, 60.0, 1.5
            # arms: zero torque (freely backdrivable)
            for i in ARM_INDICES:
                mc = self._cmd.motor_cmd[i]
                mc.q, mc.dq, mc.tau, mc.kp, mc.kd = 0.0, 0.0, 0.0, 0.0, 0.0
            self.ok = True
            print("[recorder] waist-locker ready")
        except Exception as exc:
            print(f"[recorder] waist-locker init failed: {exc}", file=sys.stderr)

    def publish(self):
        if not self.ok:
            return
        self._cmd.crc = self._crc.Crc(self._cmd)
        self._pub.Write(self._cmd)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

YELLOW = "#f4c430"
GREEN = "#5fd35f"
RED = "#e05a5a"
DIM = "#7a7a7a"


class Recorder(QtWidgets.QMainWindow):
    def __init__(self, reader: LowStateReader, locker: WaistLocker, arm: str):
        super().__init__()
        self.reader = reader
        self.locker = locker
        self.arm = arm  # "left" | "right"

        self.setWindowTitle("ArmTrain Recorder")
        self.resize(1180, 760)

        # recording state
        self.recording = False
        self.phase = "idle"            # idle|countdown|announce|window|rating
        self.phase_started = 0.0
        self.sample_idx = 0
        self.total_samples = 20
        self.window_secs = 2.0
        self.cur_direction = None
        self.start_pose: list[float] | None = None
        self.stats = {d: 0 for d in DIRECTIONS}
        self.csv_path: Path | None = None

        # session + quality bookkeeping
        self.session_id: str = ""
        self._window_buf: list[list[float]] = []
        self._pending: dict | None = None
        self._session_qualities: list[float] = []

        # balanced shuffle-bag so every direction gets equal coverage instead
        # of the lopsided counts pure random-with-replacement would give.
        self._dir_bag: list[str] = []

        self._build_ui()
        self._apply_theme()
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        # live joint monitor refresh
        self._mon_timer = QtCore.QTimer(self)
        self._mon_timer.timeout.connect(self._refresh_monitor)
        self._mon_timer.start(50)  # 20 Hz

        # recording state machine
        self._sm_timer = QtCore.QTimer(self)
        self._sm_timer.timeout.connect(self._tick)
        self._sm_timer.start(30)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)

        # ---- joint monitor -------------------------------------------------
        mon_box = QtWidgets.QGroupBox("G-1 Arm Joint Monitor")
        mon = QtWidgets.QHBoxLayout(mon_box)
        mon.setSpacing(24)

        self._joint_labels: dict[int, QtWidgets.QLabel] = {}

        self._col_left = self._make_joint_column("Left arm", LEFT)
        self._col_right = self._make_joint_column("Right arm", RIGHT)
        mon.addLayout(self._col_left["layout"], 3)
        mon.addLayout(self._col_right["layout"], 3)
        root.addWidget(mon_box, 3)

        # ---- big action display -------------------------------------------
        self.action_label = QtWidgets.QLabel("READY")
        f = QtGui.QFont()
        f.setPointSize(56)
        f.setBold(True)
        self.action_label.setFont(f)
        self.action_label.setAlignment(QtCore.Qt.AlignCenter)
        self.action_label.setMinimumHeight(120)
        root.addWidget(self.action_label)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(14)
        root.addWidget(self.progress)

        # ---- controls ------------------------------------------------------
        ctrl = QtWidgets.QHBoxLayout()
        ctrl.setSpacing(16)

        ctrl.addWidget(QtWidgets.QLabel("Arm:"))
        self.arm_combo = QtWidgets.QComboBox()
        self.arm_combo.addItems(["left", "right"])
        self.arm_combo.setCurrentText(self.arm)
        self.arm_combo.currentTextChanged.connect(self._on_arm_changed)
        ctrl.addWidget(self.arm_combo)

        ctrl.addWidget(QtWidgets.QLabel("Samples:"))
        self.samples_spin = QtWidgets.QSpinBox()
        self.samples_spin.setRange(1, 500)
        self.samples_spin.setValue(20)
        ctrl.addWidget(self.samples_spin)

        ctrl.addWidget(QtWidgets.QLabel("Sec/action:"))
        self.secs_spin = QtWidgets.QDoubleSpinBox()
        self.secs_spin.setRange(0.5, 10.0)
        self.secs_spin.setSingleStep(0.5)
        self.secs_spin.setValue(2.0)
        ctrl.addWidget(self.secs_spin)

        ctrl.addStretch(1)

        self.start_btn = QtWidgets.QPushButton("▶  Start")
        self.start_btn.setObjectName("startBtn")
        self.start_btn.clicked.connect(self.start_recording)
        ctrl.addWidget(self.start_btn)

        self.stop_btn = QtWidgets.QPushButton("■  Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_recording)
        ctrl.addWidget(self.stop_btn)

        root.addLayout(ctrl)

        # ---- stats + log ---------------------------------------------------
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(16)

        stats_box = QtWidgets.QGroupBox("Samples per direction")
        sgrid = QtWidgets.QGridLayout(stats_box)
        sgrid.setVerticalSpacing(2)
        self._stat_labels: dict[str, QtWidgets.QLabel] = {}
        for r, d in enumerate(DIRECTIONS):
            name = QtWidgets.QLabel(f"{d}:")
            name.setStyleSheet("font-family: monospace;")
            val = QtWidgets.QLabel("0   ( 0.0 %)")
            val.setStyleSheet("font-family: monospace;")
            self._stat_labels[d] = val
            sgrid.addWidget(name, r, 0)
            sgrid.addWidget(val, r, 1)
        self._total_label = QtWidgets.QLabel("total: 0")
        self._total_label.setStyleSheet("font-family: monospace; font-weight: bold;")
        sgrid.addWidget(self._total_label, len(DIRECTIONS), 0, 1, 2)
        bottom.addWidget(stats_box, 2)

        log_box = QtWidgets.QGroupBox("Log")
        lv = QtWidgets.QVBoxLayout(log_box)
        self.log_view = QtWidgets.QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet("font-family: monospace; font-size: 12px;")
        lv.addWidget(self.log_view)
        bottom.addWidget(log_box, 3)

        root.addLayout(bottom, 2)

        # status bar
        self.status = self.statusBar()
        self._update_status()
        self._highlight_active_column()

    def _make_joint_column(self, title: str, joints):
        layout = QtWidgets.QVBoxLayout()
        header = QtWidgets.QLabel(title)
        hf = QtGui.QFont()
        hf.setPointSize(16)
        hf.setBold(True)
        header.setFont(hf)
        layout.addWidget(header)
        for idx, name in joints:
            row = QtWidgets.QHBoxLayout()
            num = QtWidgets.QLabel(f"{idx}")
            num.setStyleSheet(f"color: {YELLOW}; font-weight: bold;")
            num.setFixedWidth(28)
            lbl = QtWidgets.QLabel(name)
            lbl.setMinimumWidth(150)
            val = QtWidgets.QLabel("+0.000")
            val.setStyleSheet("font-family: monospace;")
            val.setAlignment(QtCore.Qt.AlignRight)
            self._joint_labels[idx] = val
            row.addWidget(num)
            row.addWidget(lbl)
            row.addStretch(1)
            row.addWidget(val)
            layout.addLayout(row)
        layout.addStretch(1)
        return {"layout": layout, "header": header}

    def _apply_theme(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #1e1e1e; color: #e6e6e6; }
            QGroupBox {
                border: 1px solid #3a3a3a; border-radius: 6px;
                margin-top: 10px; padding-top: 8px; font-weight: bold;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
            QPushButton {
                background: #2d2d2d; border: 1px solid #444; border-radius: 5px;
                padding: 8px 18px; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background: #383838; }
            QPushButton:disabled { color: #666; }
            QPushButton#startBtn { background: #2f5d2f; }
            QPushButton#startBtn:hover { background: #3a7a3a; }
            QPushButton#stopBtn { background: #5d2f2f; }
            QPushButton#stopBtn:hover { background: #7a3a3a; }
            QProgressBar { background: #2a2a2a; border: none; border-radius: 7px; }
            QProgressBar::chunk { background: #f4c430; border-radius: 7px; }
            QComboBox, QSpinBox, QDoubleSpinBox {
                background: #2a2a2a; border: 1px solid #444; border-radius: 4px; padding: 3px 6px;
            }
            QPlainTextEdit { background: #141414; border: 1px solid #333; }
            """
        )

    # -------------------------------------------------------------- helpers
    def _arm_joint_indices(self) -> list[int]:
        joints = LEFT if self.arm == "left" else RIGHT
        return [i for i, _ in joints]

    def _record_indices(self) -> list[int]:
        return self._arm_joint_indices()

    def _highlight_active_column(self):
        left_on = self.arm == "left"
        self._col_left["header"].setStyleSheet(
            f"color: {GREEN};" if left_on else f"color: {DIM};"
        )
        self._col_right["header"].setStyleSheet(
            f"color: {GREEN};" if not left_on else f"color: {DIM};"
        )

    def _update_status(self):
        conn = (
            f"lowstate: {self.reader.msg_count} msgs"
            if self.reader.ok
            else "lowstate: NOT CONNECTED"
        )
        self.status.showMessage(
            f"arm: {self.arm}   |   {conn}   |   file: {self._current_csv_path()}"
        )

    def _current_csv_path(self) -> Path:
        return Path("data") / "arms" / self.arm / "training_data_with_waist.csv"

    def log(self, msg: str):
        self.log_view.appendPlainText(msg)
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    # ------------------------------------------------------- monitor refresh
    def _refresh_monitor(self):
        for idx, lbl in self._joint_labels.items():
            v = self.reader.joint_cur.get(idx)
            if v is None:
                lbl.setText("  --  ")
            else:
                lbl.setText(f"{v:+.3f}")
        if not self.recording:
            self._update_status()

    def _on_arm_changed(self, txt: str):
        if self.recording:
            return
        self.arm = txt
        self.stats = {d: 0 for d in DIRECTIONS}
        self._refresh_stats()
        self._highlight_active_column()
        self._update_status()

    # ------------------------------------------------------------ recording
    def start_recording(self):
        if self.recording:
            return
        if not self.reader.ok:
            QtWidgets.QMessageBox.warning(
                self, "No feedback",
                "Not receiving rt/lowstate — cannot record joint angles.\n"
                "Check the robot connection and --iface.",
            )
            return
        self.total_samples = self.samples_spin.value()
        self.window_secs = self.secs_spin.value()
        self.sample_idx = 0
        self.recording = True
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._session_qualities = []
        self._pending = None
        self._dir_bag = []
        self.cur_direction = None

        # prepare CSV
        self.csv_path = self._current_csv_path()
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.csv_path.exists():
            rec_idx = self._record_indices()
            header = (
                ["direction", "session_id", "quality"]
                + [f"start_{i}" for i in rec_idx]
                + [f"end_{i}" for i in rec_idx]
            )
            with self.csv_path.open("w", newline="") as fp:
                csv.writer(fp).writerow(header)
        # mark the start of this take with a comment row (skipped by training)
        with self.csv_path.open("a", newline="") as fp:
            csv.writer(fp).writerow(
                [f"# session {self.session_id} | arm={self.arm} | "
                 f"target={self.total_samples} samples | started"]
            )
        # load existing stats from file so counters continue
        self._load_existing_stats()

        self.arm_combo.setEnabled(False)
        self.samples_spin.setEnabled(False)
        self.secs_spin.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.log(f"=== session {self.session_id} — "
                 f"{self.total_samples} samples for {self.arm} arm ===")
        self.log(f"file: {self.csv_path}")
        self.log("tip: after each sample press 1-5 to override the auto quality "
                 "score (or do nothing to keep it)")
        self._enter_phase("countdown")

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        self.phase = "idle"
        self._pending = None  # discard any un-committed sample

        n = len(self._session_qualities)
        if n > 0:
            reply = QtWidgets.QMessageBox.question(
                self,
                "Save session?",
                f"Save {n} sample{'s' if n != 1 else ''} from this session?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes,
            )
            if reply == QtWidgets.QMessageBox.Yes:
                self._write_session_footer()
                self.log(f"=== stopped — {n} samples saved ===")
            else:
                self._discard_session()
                self.log(f"=== stopped — session discarded ({n} samples removed) ===")
        else:
            self.log("=== stopped — no samples to save ===")

        self.progress.setValue(0)
        self.action_label.setText("STOPPED")
        self.action_label.setStyleSheet(f"color: {RED};")
        self.arm_combo.setEnabled(True)
        self.samples_spin.setEnabled(True)
        self.secs_spin.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)

    def _enter_phase(self, phase: str):
        self.phase = phase
        self.phase_started = time.monotonic()
        if phase == "countdown":
            self.action_label.setStyleSheet(f"color: {YELLOW};")
            self.action_label.setText("3")
            speak("3")
            self._countdown_spoken = {3}
        elif phase == "announce":
            # snapshot start pose, choose + speak direction
            self.start_pose = self.reader.snapshot(self._record_indices())
            self.cur_direction = self._next_direction()
            self.action_label.setStyleSheet("color: #ffffff;")
            self.action_label.setText(GLYPH[self.cur_direction])
            speak(SPEAK[self.cur_direction])
            self.log(
                f"[sample {self.sample_idx + 1}/{self.total_samples}] "
                f"action → {self.cur_direction}"
            )
            self._enter_phase("window")
        elif phase == "window":
            self.progress.setValue(0)
            self._window_buf = []
        elif phase == "rating":
            # score already computed into self._pending; flash it and let the
            # user optionally override with 1-5 for RATING_SECS.
            self.progress.setValue(1000)
            self._show_quality(self._pending["quality"])
            self.setFocus()  # ensure key events land on the window

    def _tick(self):
        self.locker.publish()

        if not self.recording or self.phase == "idle":
            return

        elapsed = time.monotonic() - self.phase_started

        if self.phase == "countdown":
            remaining = 3 - int(elapsed)
            if remaining >= 1:
                if remaining not in self._countdown_spoken:
                    self._countdown_spoken.add(remaining)
                    self.action_label.setText(str(remaining))
                    speak(str(remaining))
            if elapsed >= 3.0:
                speak("go")
                self._enter_phase("announce")

        elif self.phase == "window":
            # continuously sample so we can judge end-pose stability
            self._window_buf.append(self.reader.snapshot(self._record_indices()))
            frac = min(1.0, elapsed / self.window_secs)
            self.progress.setValue(int(frac * 1000))
            if elapsed >= self.window_secs:
                self._finish_sample()

        elif self.phase == "rating":
            if elapsed >= RATING_SECS:
                self._commit_sample(self._pending["quality"], manual=False)

    def _finish_sample(self):
        end_pose = self.reader.snapshot(self._record_indices())
        quality, disp, std = self._compute_quality(
            self.start_pose, end_pose, self._window_buf
        )
        # Stash the sample; it is written only after the rating window so a
        # manual 1-5 override can change the stored score first.
        self._pending = {
            "direction": self.cur_direction,
            "start": list(self.start_pose),
            "end": list(end_pose),
            "quality": quality,
        }
        self.log(
            f"    auto quality={quality:.2f}  (moved {disp:.3f} rad, "
            f"end jitter {std:.3f} rad)"
        )
        self._enter_phase("rating")

    def _next_direction(self) -> str:
        """Draw the next direction from a shuffled bag of all 6 (equal coverage).

        Refills + reshuffles when empty, avoiding an immediate repeat across
        bag boundaries.
        """
        if not self._dir_bag:
            bag = list(DIRECTIONS)
            random.shuffle(bag)
            # pop() draws from the end, so guard the *last* element against an
            # immediate repeat across the bag boundary.
            if self.cur_direction is not None and bag[-1] == self.cur_direction and len(bag) > 1:
                bag[-1], bag[-2] = bag[-2], bag[-1]
            self._dir_bag = bag
        return self._dir_bag.pop()

    def _compute_quality(self, start_pose, end_pose, window_buf):
        """Return (quality 0-1, displacement rad, end-jitter rad).

        Arm joints only (waist at index 0 of the record snapshot is ignored).
        """
        start = np.asarray(start_pose, dtype=float)
        end = np.asarray(end_pose, dtype=float)
        disp = float(np.linalg.norm(end - start))
        disp_score = min(1.0, disp / QUAL_DISP_FULL)

        std_mean = 0.0
        if window_buf:
            n = min(len(window_buf), QUAL_STAB_SAMPLES)
            tail = np.asarray([w[1:] for w in window_buf[-n:]], dtype=float)
            if tail.shape[0] >= 2:
                std_mean = float(tail.std(axis=0).mean())
        stab_score = max(0.0, 1.0 - std_mean / QUAL_STAB_ZERO)

        quality = W_DISP * disp_score + W_STAB * stab_score
        return round(quality, 2), disp, std_mean

    def _show_quality(self, q: float):
        filled = int(round(q * 5))
        stars = "★" * filled + "☆" * (5 - filled)
        col = GREEN if q >= 0.6 else (YELLOW if q >= 0.3 else RED)
        self.action_label.setStyleSheet(f"color: {col};")
        self.action_label.setText(f"{stars}   {q:.2f}")

    def _commit_sample(self, quality: float, manual: bool):
        if self._pending is None:
            return
        p = self._pending
        self._pending = None
        row = (
            [p["direction"], self.session_id, f"{quality:.2f}"]
            + list(p["start"])
            + list(p["end"])
        )
        with self.csv_path.open("a", newline="") as fp:
            csv.writer(fp).writerow(
                [f"{v:.6f}" if isinstance(v, float) else v for v in row]
            )
        self.stats[p["direction"]] += 1
        self._session_qualities.append(quality)
        self._refresh_stats()
        tag = "manual" if manual else "auto"
        self.log(f"  ✓ sample saved  quality={quality:.2f} ({tag})")
        self.sample_idx += 1

        if self.sample_idx >= self.total_samples:
            self._finish_run()
        else:
            self._enter_phase("announce")

    def _write_session_footer(self):
        if not self.csv_path or not self._session_qualities:
            return
        n = len(self._session_qualities)
        avg = sum(self._session_qualities) / n
        with self.csv_path.open("a", newline="") as fp:
            csv.writer(fp).writerow(
                [f"# session {self.session_id} | {n} samples saved | "
                 f"avg quality {avg:.2f} | ended"]
            )

    def _discard_session(self):
        """Remove all rows belonging to this session from the CSV."""
        if not self.csv_path or not self.csv_path.exists():
            return
        sid = self.session_id
        kept = []
        try:
            with self.csv_path.open("r", newline="") as fp:
                for line in fp:
                    # drop data rows and the session header/footer for this session
                    if sid in line:
                        continue
                    kept.append(line)
            with self.csv_path.open("w", newline="") as fp:
                fp.writelines(kept)
        except Exception as exc:
            self.log(f"[warn] could not discard session from CSV: {exc}")

    def keyPressEvent(self, ev):
        # 1-5 overrides the auto quality during the rating flash.
        if self.recording and self.phase == "rating" and self._pending is not None:
            override = {
                QtCore.Qt.Key_1: 0.2,
                QtCore.Qt.Key_2: 0.4,
                QtCore.Qt.Key_3: 0.6,
                QtCore.Qt.Key_4: 0.8,
                QtCore.Qt.Key_5: 1.0,
            }.get(ev.key())
            if override is not None:
                self._commit_sample(override, manual=True)
                return
        super().keyPressEvent(ev)

    def _finish_run(self):
        self.recording = False
        self.phase = "idle"
        self._write_session_footer()
        self.progress.setValue(1000)
        self.action_label.setStyleSheet(f"color: {GREEN};")
        self.action_label.setText("DONE ✓")
        speak("done")
        self.arm_combo.setEnabled(True)
        self.samples_spin.setEnabled(True)
        self.secs_spin.setEnabled(True)
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log(f"=== finished: {self.sample_idx} samples for {self.arm} arm ===")

    def _refresh_stats(self):
        total = sum(self.stats.values())
        for d in DIRECTIONS:
            n = self.stats[d]
            pct = (100.0 * n / total) if total else 0.0
            colour = GREEN if n else DIM
            self._stat_labels[d].setText(f"{n:>4d}   ({pct:5.1f} %)")
            self._stat_labels[d].setStyleSheet(f"font-family: monospace; color: {colour};")
        self._total_label.setText(f"total: {total}")

    def _load_existing_stats(self):
        self.stats = {d: 0 for d in DIRECTIONS}
        try:
            with self.csv_path.open("r", newline="") as fp:
                rdr = csv.DictReader(fp)
                for r in rdr:
                    d = r.get("direction")
                    if d in self.stats:
                        self.stats[d] += 1
        except Exception:
            pass
        self._refresh_stats()

    def closeEvent(self, ev):
        self.recording = False
        super().closeEvent(ev)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description="G-1 ArmTrain data recorder")
    ap.add_argument("--iface", default="enxa0cec8b8657b", help="DDS network interface")
    ap.add_argument("--domain", type=int, default=0, help="DDS domain id")
    ap.add_argument("--arm", choices=["left", "right"], default="left")
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)

    reader = LowStateReader(args.iface, args.domain)
    locker = WaistLocker()

    win = Recorder(reader, locker, args.arm)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
