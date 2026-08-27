#!/usr/bin/env python3
"""Hand tuner GUI — real-time slider control of Inspire RH56 hands.

Auto-detects which USB-RS485 ports are live on startup. If a hand is
plugged in later, use the RECONNECT button to try again without restarting.

    uv run python src/tuner_gui.py

Pose configs are saved to config/poses/ as YAML files.
"""

from __future__ import annotations

import glob
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSES_DIR    = PROJECT_ROOT / "config" / "poses"
POSES_DIR.mkdir(parents=True, exist_ok=True)

JOINT_NAMES = ["pinky", "ring", "middle", "index", "thumb_bend", "thumb_rot"]
POS_MAX = 2000
POS_MIN = 0
SEND_INTERVAL = 0.04   # 25 Hz

# Fixed port assignments — left is always ttyUSB0, right always ttyUSB1
SIDE_PORTS = {"left": "/dev/ttyUSB0", "right": "/dev/ttyUSB1"}


def _available_ports() -> list[str]:
    return sorted(glob.glob("/dev/ttyUSB*"))


class HandChannel:
    def __init__(self, side: str, baud: int = 115200):
        self.side         = side
        self.port         = SIDE_PORTS[side]
        self.baud         = baud
        self.hand         = None
        self.error        = None
        self.initial_pos  = [0] * 6
        self._lock        = threading.Lock()
        self._last_sent: list[int] | None = None
        self._last_send_t = 0.0
        self._try_connect()

    def _try_connect(self) -> bool:
        """Attempt to open the serial port. Returns True on success."""
        try:
            from dexkit.hands import InspireHand
            from dexkit.hands.inspire_hand import REG_POS_ACT
            h = InspireHand(port=self.port, baud=self.baud, hand_id=1)
            h.set_speed([1000] * 6)
            h.set_force([400]  * 6)
            pos = h._read_shorts(REG_POS_ACT, count=6)
            with self._lock:
                if self.hand:
                    self.hand.close()
                self.hand        = h
                self.initial_pos = pos or [0] * 6
                self.error       = None
            return True
        except Exception as e:
            self.error = str(e)
            self.hand  = None
            return False

    def reconnect(self) -> bool:
        return self._try_connect()

    def send(self, positions: list[int]) -> None:
        if self.hand is None:
            return
        now = time.monotonic()
        if now - self._last_send_t < SEND_INTERVAL:
            return
        if positions == self._last_sent:
            return
        with self._lock:
            try:
                self.hand.set_positions(positions)
                self._last_sent   = list(positions)
                self._last_send_t = now
            except Exception:
                pass

    def read_pos(self) -> list[int] | None:
        if self.hand is None:
            return None
        from dexkit.hands.inspire_hand import REG_POS_ACT
        with self._lock:
            try:
                return self.hand._read_shorts(REG_POS_ACT, count=6)
            except Exception:
                return None

    def open_hand(self) -> None:
        if self.hand:
            with self._lock:
                self.hand.open_hand()

    def close(self) -> None:
        if self.hand:
            with self._lock:
                self.hand.close()
            self.hand = None


class TunerApp(tk.Tk):
    def __init__(self, baud: int = 115200):
        super().__init__()
        self.title("Inspire Hand Tuner")
        self.resizable(True, True)
        self.configure(bg="#1e1e1e")
        self.baud = baud

        self.channels: dict[str, HandChannel] = {
            side: HandChannel(side, baud) for side in ("left", "right")
        }
        self.vars: dict[str, list[tk.IntVar]] = {}
        self._status_labels: dict[str, tk.Label] = {}

        self._build_ui()
        self._init_sliders()
        self._send_loop()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _build_ui(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TScale", background="#1e1e1e", troughcolor="#444",
                        sliderlength=22, sliderrelief="flat")

        # Hand columns
        hands_frame = tk.Frame(self, bg="#1e1e1e")
        hands_frame.pack(fill="both", expand=True, padx=10, pady=10)

        for col, side in enumerate(("left", "right")):
            self.vars[side] = [tk.IntVar(value=0) for _ in JOINT_NAMES]
            self._build_hand_column(hands_frame, side, col)

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=6)
        self._build_pose_panel()

        self.status_var = tk.StringVar(value="Ready")
        tk.Label(self, textvariable=self.status_var, bg="#1e1e1e", fg="#888",
                 font=("Consolas", 9), anchor="w").pack(fill="x", padx=10, pady=(0, 4))

    def _build_hand_column(self, parent, side: str, col: int):
        ch  = self.channels[side]
        frm = tk.Frame(parent, bg="#2a2a2a", bd=1, relief="groove")
        frm.grid(row=0, column=col, padx=8, sticky="nsew")
        parent.columnconfigure(col, weight=1)

        # Header
        tk.Label(frm, text=f"{side.upper()}   {ch.port}", bg="#2a2a2a",
                 fg="#fff", font=("Consolas", 12, "bold")).pack(pady=(8, 0))

        connected = ch.hand is not None
        lbl = tk.Label(frm,
                       text="✓ connected" if connected else f"✗ {ch.error or 'not found'}",
                       bg="#2a2a2a",
                       fg="#4caf50" if connected else "#f44336",
                       font=("Consolas", 9))
        lbl.pack(pady=(0, 6))
        self._status_labels[side] = lbl

        # Sliders
        for dof, name in enumerate(JOINT_NAMES):
            row = tk.Frame(frm, bg="#2a2a2a")
            row.pack(fill="x", padx=10, pady=3)

            tk.Label(row, text=f"{name:<12}", bg="#2a2a2a", fg="#ccc",
                     font=("Consolas", 10), width=12, anchor="w").pack(side="left")

            val_lbl = tk.Label(row, text="0", bg="#2a2a2a", fg="#ffcc00",
                               font=("Consolas", 10), width=5, anchor="e")
            val_lbl.pack(side="right")

            var = self.vars[side][dof]
            var.trace_add("write",
                lambda *_, v=val_lbl, vr=var: v.config(text=str(vr.get())))

            ttk.Scale(row, from_=POS_MIN, to=POS_MAX, orient="horizontal",
                      variable=var, length=300).pack(
                side="left", fill="x", expand=True, padx=4)

        # Buttons
        btn_row = tk.Frame(frm, bg="#2a2a2a")
        btn_row.pack(pady=8)
        for label, cmd, bg in [
            ("OPEN",       lambda s=side: self._open_hand(s),   "#333"),
            ("READ",       lambda s=side: self._read_hand(s),   "#333"),
            ("MIRROR→",    lambda s=side: self._mirror(s),      "#2e4a2e"),
            ("RECONNECT",  lambda s=side: self._reconnect(s),   "#4a3800"),
        ]:
            tk.Button(btn_row, text=label, width=9, bg=bg, fg="#fff",
                      font=("Consolas", 9), command=cmd).pack(side="left", padx=2)

    def _build_pose_panel(self):
        frm = tk.Frame(self, bg="#1e1e1e")
        frm.pack(fill="x", padx=10, pady=4)

        tk.Label(frm, text="Pose:", bg="#1e1e1e", fg="#ccc",
                 font=("Consolas", 11)).pack(side="left")

        self.pose_name_var = tk.StringVar(value="index_pinch")

        # Dropdown of saved poses + free-text entry
        self._pose_combo = ttk.Combobox(frm, textvariable=self.pose_name_var,
                                        width=22, font=("Consolas", 11))
        self._pose_combo.pack(side="left", padx=6)
        self._refresh_poses_list()

        for label, cmd, bg in [
            ("SAVE",  self._save_pose,  "#1565c0"),
            ("LOAD",  self._load_pose,  "#4a148c"),
        ]:
            tk.Button(frm, text=label, bg=bg, fg="#fff",
                      font=("Consolas", 10), command=cmd).pack(side="left", padx=3)

    # ------------------------------------------------------------------
    # Slider init & send loop
    # ------------------------------------------------------------------

    def _init_sliders(self):
        for side, ch in self.channels.items():
            for dof, val in enumerate(ch.initial_pos):
                self.vars[side][dof].set(max(POS_MIN, min(POS_MAX, val)))

    def _send_loop(self):
        for side, ch in self.channels.items():
            pos = [self.vars[side][d].get() for d in range(6)]
            ch.send(pos)
        self.after(40, self._send_loop)

    # ------------------------------------------------------------------
    # Button actions
    # ------------------------------------------------------------------

    def _open_hand(self, side: str):
        self.channels[side].open_hand()
        for d in range(6):
            self.vars[side][d].set(0)
        self.status_var.set(f"{side} opened")

    def _read_hand(self, side: str):
        pos = self.channels[side].read_pos()
        if pos:
            for d, v in enumerate(pos):
                self.vars[side][d].set(max(POS_MIN, min(POS_MAX, v)))
            self.status_var.set(f"{side} read: {pos}")
        else:
            self.status_var.set(f"{side}: read failed (not connected?)")

    def _mirror(self, source: str):
        target = "right" if source == "left" else "left"
        for d in range(6):
            val = self.vars[source][d].get()
            if d == 5:
                val = POS_MAX - val
            self.vars[target][d].set(val)
        self.status_var.set(f"mirrored {source} → {target}")

    def _reconnect(self, side: str):
        ch = self.channels[side]
        ok = ch.reconnect()
        lbl = self._status_labels[side]
        if ok:
            lbl.config(text="✓ connected", fg="#4caf50")
            self._init_sliders()
            self.status_var.set(f"{side} reconnected on {ch.port}")
        else:
            lbl.config(text=f"✗ {ch.error or 'not found'}", fg="#f44336")
            self.status_var.set(f"{side}: reconnect failed — {ch.error}")

    # ------------------------------------------------------------------
    # Pose save / load
    # ------------------------------------------------------------------

    def _save_pose(self):
        name = self.pose_name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Enter a pose name.")
            return
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        path = POSES_DIR / f"{safe}.yaml"
        pose = {side: [self.vars[side][d].get() for d in range(6)]
                for side in self.channels}
        path.write_text(yaml.dump({
            "name":         name,
            "positions":    pose,
            "joint_order":  JOINT_NAMES,
            "scale":        "POS 0=open 2000=bent",
        }, default_flow_style=False))
        self.status_var.set(f"saved → {path.name}")
        self._refresh_poses_list()

    def _load_pose(self):
        name = self.pose_name_var.get().strip()
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        path = POSES_DIR / f"{safe}.yaml"
        if not path.exists():
            matches = list(POSES_DIR.glob(f"*{safe}*.yaml"))
            if not matches:
                messagebox.showerror("Not found", f"No pose matching '{name}'.")
                return
            path = matches[0]
        data      = yaml.safe_load(path.read_text())
        positions = data.get("positions", {})
        for side, vals in positions.items():
            if side in self.vars and len(vals) == 6:
                for d, v in enumerate(vals):
                    self.vars[side][d].set(max(POS_MIN, min(POS_MAX, v)))
        self.pose_name_var.set(data.get("name", name))
        self.status_var.set(f"loaded ← {path.name}")

    def _refresh_poses_list(self):
        names = [p.stem for p in sorted(POSES_DIR.glob("*.yaml"))]
        if hasattr(self, "_pose_combo"):
            self._pose_combo["values"] = names

    # ------------------------------------------------------------------

    def on_close(self):
        for ch in self.channels.values():
            ch.close()
        self.destroy()


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Inspire hand tuner GUI")
    ap.add_argument("--baud", type=int, default=115200)
    args = ap.parse_args()

    app = TunerApp(baud=args.baud)
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
