#!/usr/bin/env python3
"""Animation recorder for hand_preset_mimic — Flet (Material 3) UI.

The takes list IS the sequence — items play top to bottom.
Combined items play their children in parallel.

Workflow:
  1. Move sliders → hand follows live at 25 Hz
  2. REC → move one set of joints → STOP  (creates a Take)
  3. Repeat for other joint groups
  4. Check 2+ takes → COMBINE → parallel group
  5. Reorder with ↑↓ to set the sequence
  6. SAVE PRESET → concatenates all items into one animation.yaml

Usage:
    uv run python src/recorder.py
    uv run python src/recorder.py --dry-run   # UI only, no hardware
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import flet as ft
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRESETS_DIR  = PROJECT_ROOT / "presets"
GESTURES     = ["2finger_pinch", "3finger_pinch", "grab"]
PERMANENT    = {"2finger_pinch", "3finger_pinch", "grab"}  # cannot be removed

JOINT_NAMES = ["pinky", "ring", "middle", "index", "thumb_bend", "thumb_rot"]
SCALE_MAX   = 1000
SCALE_MIN   = 0
SEND_HZ     = 25
SEND_DT     = 1.0 / SEND_HZ
REC_HZ      = 25
REC_DT      = 1.0 / REC_HZ

SIDE_PORTS = {"left": "/dev/ttyUSB0", "right": "/dev/ttyUSB1"}
SERIAL_MAP_FILE = PROJECT_ROOT / "config" / "serial_map.json"


def scan_serial_ports():
    """Return USB serial ports likely to be hands, as list of
    {device, sn, label}. Filters to USB adapters (RS-485 dongles)."""
    try:
        from serial.tools import list_ports
    except Exception:
        return []
    out = []
    for p in sorted(list_ports.comports(), key=lambda x: x.device):
        # USB serial adapters have a vid; built-in serial ports don't
        if getattr(p, "vid", None) is None and "USB" not in (p.hwid or ""):
            continue
        desc = (p.description or "").strip()
        sn   = getattr(p, "serial_number", None) or ""
        out.append({"device": p.device, "sn": sn,
                    "label": f"{p.device}"})
    return out


def _load_serial_map() -> dict:
    try:
        import json
        return json.loads(SERIAL_MAP_FILE.read_text())
    except Exception:
        return {}


def _save_serial_map(mapping: dict):
    try:
        import json
        SERIAL_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
        SERIAL_MAP_FILE.write_text(json.dumps(mapping, indent=2))
    except Exception:
        pass

MOVE_THRESHOLD = 15        # min joint travel for a take to count as "moving"
SEGMENT_SETTLE_SEC = 0.5   # wall-clock hold at each take boundary for servos to settle

# ---- Themes: "blue" (default) and "bw" (black & white) ----
THEMES = {
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

# Module-level palette globals — populated by _apply_theme()
BG = CARD = CARD2 = ITEM = BORDER = FG = FG_DIM = FG_VAL = ""
LBL = SUBTITLE = ""
OK = ERR = ACCENT = TEAL = INDIGO = PURPLE = NEUTRAL = DANGER = GREEN = ""
SLIDER_ACTIVE = SLIDER_THUMB = SPEED_ACTIVE = SPEED_THUMB = ""
EMPTY_BG = EMPTY_BORDER = EMPTY_ICON = EMPTY_TEXT = ""


def _apply_theme(name: str):
    pal = THEMES.get(name, THEMES["blue"])
    g = globals()
    for k, v in pal.items():
        g[k] = v
    g["GREEN"] = pal["TEAL"]


_apply_theme("blue")


# ---------------------------------------------------------------------------
# Hand channel
# ---------------------------------------------------------------------------

class HandChannel:
    def __init__(self, side: str, port: str | None = None, baud: int = 115200):
        self.side  = side
        self.port  = port or SIDE_PORTS[side]
        self.baud  = baud
        self.hand  = None
        self.error: str | None = None
        self._lock = threading.Lock()
        self._last_sent: list[int] | None = None
        self._last_t = 0.0
        self._try_connect()

    def set_port(self, port: str) -> bool:
        """Switch to a new serial port and reconnect."""
        self.port = port
        return self._try_connect()

    def _try_connect(self) -> bool:
        try:
            from dexkit.hands import InspireHand
            h = InspireHand(port=self.port, baud=self.baud, hand_id=1)
            h.set_speed([800] * 6)
            h.set_force([400] * 6)
            with self._lock:
                if self.hand:
                    self.hand.close()
                self.hand  = h
                self.error = None
            return True
        except Exception as e:
            self.error = str(e)
            self.hand  = None
            return False

    def reconnect(self) -> bool:
        return self._try_connect()

    def send(self, angles: list[int], immediate: bool = False) -> None:
        if self.hand is None:
            return
        now = time.monotonic()
        if not immediate and now - self._last_t < SEND_DT:
            return
        if not immediate and angles == self._last_sent:
            return
        with self._lock:
            try:
                self.hand.set_angles(angles)
                self._last_sent = list(angles)
                self._last_t    = now
            except Exception:
                pass

    def set_motor_speed(self, v: int) -> None:
        if self.hand is None:
            return
        v = max(1, min(1000, int(v)))
        with self._lock:
            try:
                self.hand.set_speed([v] * 6)
            except Exception:
                pass

    def close(self) -> None:
        if self.hand:
            with self._lock:
                self.hand.close()
            self.hand = None


# ---------------------------------------------------------------------------
# Take data model + processing (unchanged logic)
# ---------------------------------------------------------------------------

class Take:
    _counter = 0

    def __init__(self, frames: list[dict[str, list[int]]], fps: float = REC_HZ):
        Take._counter += 1
        self.name   = f"Take {Take._counter}"
        self.frames = frames
        self.fps    = fps

    @property
    def duration(self) -> float:
        return len(self.frames) / self.fps if self.fps > 0 else 0.0

    def frame_at(self, idx: int) -> dict[str, list[int]]:
        return self.frames[min(idx, len(self.frames) - 1)]

    def resample(self, target_fps: float) -> "Take":
        if not self.frames:
            t = Take([], target_fps); t.name = self.name; return t
        n   = max(1, round(self.duration * target_fps))
        out = [self.frames[min(round(i * self.fps / target_fps),
                               len(self.frames) - 1)] for i in range(n)]
        t = Take(out, target_fps); t.name = self.name; return t


def _trim_static(frames, threshold: int = 5):
    if not frames:
        return frames

    def _changed(a, b):
        for side in set(a) | set(b):
            va = a.get(side, [0]*6); vb = b.get(side, [0]*6)
            if any(abs(va[j] - vb[j]) > threshold for j in range(6)):
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


def _extract_keyframes(frames, threshold: int = 8):
    if not frames:
        return []
    keys = [0]
    for i in range(1, len(frames)):
        prev = frames[keys[-1]]; cur = frames[i]
        for side in set(prev) | set(cur):
            vp = prev.get(side, [0]*6); vc = cur.get(side, [0]*6)
            if any(abs(vc[j] - vp[j]) >= threshold for j in range(6)):
                keys.append(i); break
    if keys[-1] != len(frames) - 1:
        keys.append(len(frames) - 1)
    return keys


def _lerp_frame(a, b, t):
    sides = set(a) | set(b)
    return {side: [int(round(a.get(side, [0]*6)[j] * (1 - t) +
                             b.get(side, [0]*6)[j] * t)) for j in range(6)]
            for side in sides}


def _smooth_linear(frames, keyframe_threshold: int = 8, target_fps: float = REC_HZ):
    if len(frames) < 2:
        return frames
    keys = _extract_keyframes(frames, keyframe_threshold)
    if len(keys) < 2:
        return frames

    def _seg_len(fi, fj):
        a, b = frames[fi], frames[fj]; d = 0.0
        for side in set(a) | set(b):
            va = a.get(side, [0]*6); vb = b.get(side, [0]*6)
            d = max(d, max(abs(vb[j] - va[j]) for j in range(6)))
        return max(d, 1.0)

    total_len = sum(_seg_len(keys[i], keys[i+1]) for i in range(len(keys)-1))
    total_frames = max(len(frames), 2)
    out = []
    for seg in range(len(keys) - 1):
        seg_len  = _seg_len(keys[seg], keys[seg+1])
        n_frames = max(2, round(total_frames * seg_len / total_len))
        fa, fb   = frames[keys[seg]], frames[keys[seg+1]]
        for fi in range(n_frames - (1 if seg < len(keys)-2 else 0)):
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
    prev = {s: list(frames[0].get(s, [0]*6)) for s in sides}
    for f in frames[1:]:
        nxt = {}
        for side in sides:
            raw = f.get(side, [0]*6)
            nxt[side] = [int(round(alpha * raw[j] + (1 - alpha) * prev[side][j]))
                         for j in range(6)]
        prev = nxt
        out.append(nxt)
    return out


def _resample_to(frames, n, fps: float = REC_HZ):
    if not frames or n <= 0:
        return frames
    return [frames[min(round(i * (len(frames) - 1) / max(n - 1, 1)),
                       len(frames) - 1)] for i in range(n)]


def _compose_segments(takes, threshold: int = MOVE_THRESHOLD):
    running: dict[str, list[int]] = {}
    segments = []
    for take in takes:
        frames = take.frames
        if not frames:
            continue
        sides = set()
        for f in frames:
            sides.update(f.keys())
        for side in sides:
            running.setdefault(side, list(frames[0].get(side, [SCALE_MAX] * 6)))
        active = {side: [False] * 6 for side in sides}
        for side in sides:
            cols = [f.get(side, [SCALE_MAX] * 6) for f in frames]
            for j in range(6):
                vals = [c[j] for c in cols]
                if max(vals) - min(vals) > threshold:
                    active[side][j] = True
        seg = []
        for f in frames:
            frame = {}
            for side in sides:
                fv = f.get(side, [SCALE_MAX] * 6)
                frame[side] = [fv[j] if active[side][j] else running[side][j]
                               for j in range(6)]
            seg.append(frame)
        for side in sides:
            running[side] = list(seg[-1][side])
        segments.append(seg)
    return segments


def _compose_sequence(takes, threshold: int = MOVE_THRESHOLD):
    out = []
    for seg in _compose_segments(takes, threshold):
        out.extend(seg)
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
            va = a.get(side, [0]*6); vb = b.get(side, [0]*6)
            d = max(d, max(abs(vb[j] - va[j]) for j in range(6)))
        return d

    cum = [0.0]
    for i in range(1, len(frames)):
        cum.append(cum[-1] + _dist(frames[i-1], frames[i]))
    total = cum[-1]
    if total <= 0:
        return frames

    def _interp(a, b, t):
        return {side: [int(round(a.get(side, [0]*6)[j] * (1-t) +
                                 b.get(side, [0]*6)[j] * t)) for j in range(6)]
                for side in sides}

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
    base_start = {s: [SCALE_MAX]*6 for s in sides}
    base_end   = {s: [SCALE_MAX]*6 for s in sides}
    for flist in trimmed:
        s0, s1 = flist[0], flist[-1]
        for side in sides:
            sv = s0.get(side, [SCALE_MAX]*6); ev = s1.get(side, [SCALE_MAX]*6)
            for j in range(6):
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
            frame[side] = [int(round(sv[j] + (ev[j] - sv[j]) * t_ratio))
                           for j in range(6)]
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


# ---------------------------------------------------------------------------
# Flet UI helpers
# ---------------------------------------------------------------------------

def _border(w=1, color=None):
    color = color or BORDER
    s = ft.BorderSide(w, color)
    return ft.Border(top=s, right=s, bottom=s, left=s)


def _toggle_group(options: list[str], selected_ref: list[str],
                  on_select, *, size=13, height=36) -> ft.Row:
    """A row of pill-toggle buttons — no dropdown, no overlay, no grey box.
    selected_ref[0] holds the current value and is mutated on click.
    on_select(new_value) is called after the change.
    Returns the Row; call row.update() to redraw after an external change.
    """
    btns: list[ft.Button] = []

    def _make(opt):
        is_sel = opt == selected_ref[0]
        def _click(e, v=opt, bs=btns):
            selected_ref[0] = v
            for b in bs:
                sel = b.data == v
                b.bgcolor = ACCENT if sel else NEUTRAL
                # selected = dark text on light accent; unselected = white text
                b.content.color = BG if sel else "#ffffff"
                b.elevation = 5 if sel else 2
                b.update()
            on_select(v)
        btn = ft.Button(
            content=ft.Text(opt.upper(), size=size, weight=ft.FontWeight.W_900,
                            color=BG if is_sel else "#ffffff"),
            height=height, on_click=_click, data=opt,
            bgcolor=ACCENT if is_sel else NEUTRAL, elevation=5 if is_sel else 2,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=height // 2),
                padding=ft.Padding(14, 0, 14, 0)))
        btns.append(btn)
        return btn

    return ft.Row([_make(o) for o in options], spacing=6, tight=True)


def _pill(text, on_click, color, *, expand=False, width=None, height=40, size=15):
    """Pill button: grey surface, white text, drop shadow. color param unused."""
    return ft.Button(
        content=ft.Text(text.upper(), size=size, weight=ft.FontWeight.W_900,
                        color="#ffffff"),
        on_click=on_click, width=width, height=height, expand=expand,
        bgcolor=NEUTRAL, elevation=4,
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=height // 2),
            padding=ft.Padding(18, 0, 18, 0),
            shadow_color=ft.Colors.with_opacity(0.55, "#000000"),
            elevation={"": 4, "hovered": 8, "pressed": 1}))


def _card(title, *controls, bgcolor=None):
    kids = []
    if title:
        kids.append(ft.Text(title.upper(), size=17, weight=ft.FontWeight.W_900,
                            color=ACCENT))
    kids.extend(controls)
    return ft.Container(
        content=ft.Column(kids, spacing=6),
        bgcolor=CARD2,       # darker blue floating tab
        border_radius=16, padding=ft.Padding(12, 10, 12, 10),
        border=_border(1, BORDER),
        shadow=ft.BoxShadow(blur_radius=16, spread_radius=0,
                            color=ft.Colors.with_opacity(0.45, "#0a1830"),
                            offset=ft.Offset(0, 6)),
        margin=ft.Margin(0, 0, 0, 0))


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class RecorderApp:
    def __init__(self, page: ft.Page, baud: int = 115200):
        self.page = page
        self.baud = baud

        # Resolve which serial port each hand uses (saved mapping → first two)
        self._ports = scan_serial_ports()
        left_dev, right_dev = self._resolve_default_ports()
        self.channels = {
            "left":  HandChannel("left",  left_dev,  baud),
            "right": HandChannel("right", right_dev, baud),
        }
        self.port_dds: dict[str, ft.Dropdown] = {}
        self._known_devices = [p["device"] for p in self._ports]

        # live slider state (source of truth for the send loop)
        self.slider_vals: dict[str, list[int]] = {s: [SCALE_MAX]*6 for s in ("left", "right")}
        self.sliders:   dict[str, list[ft.Slider]] = {}
        self.val_lbls:  dict[str, list[ft.Text]]   = {}
        self.conn_lbls: dict[str, ft.Text] = {}

        self._recording  = False
        self._rec_frames: list[dict[str, list[int]]] = []
        self._rec_start  = 0.0
        self._rec_last_t = 0.0

        self._playing = False
        self._play_segments: list[list[dict[str, list[int]]]] = []
        self._play_seg_idx = 0
        self._play_fps = float(REC_HZ)
        self._play_dwell_until = 0.0

        self.sequence: list[SeqItem] = []

        self.smooth_mode = "Linear"
        self.smooth_strength = 0.5
        self.gesture = GESTURES[0]
        self.speed_mult = 1.0

        self._build()
        # Run the tick loop on Flet's own event loop (like tkinter after()) so
        # slider updates flush synchronously and animate in real time.
        self.page.run_task(self._loop_async)

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self):
        p = self.page
        p.title = "Hand Animation Recorder"
        p.theme_mode = ft.ThemeMode.DARK
        p.bgcolor = BG
        p.padding = 0
        p.spacing = 0
        p.scroll = None   # columns fill the window and scroll internally
        p.horizontal_alignment = ft.CrossAxisAlignment.STRETCH
        p.theme = ft.Theme(slider_theme=ft.SliderTheme(track_height=12))
        p.window.maximized = True

        # Hands
        hands = ft.Row(
            [self._hand_card("left"), self._hand_card("right")],
            spacing=8)

        # Recording
        self.rec_btn = _pill("●  Rec", self._start_rec, DANGER, width=160, height=40, size=14)
        self.rec_btn.bgcolor = DANGER
        self.stop_btn = _pill("■  Stop", self._stop_rec, NEUTRAL, width=160, height=40, size=14)
        self.stop_btn.disabled = True
        self.rec_info = ft.Text("0 FRAMES · 0.0s", size=20, color=FG_VAL,
                                weight=ft.FontWeight.W_900, expand=True,
                                text_align=ft.TextAlign.RIGHT)
        rec_title_bar = ft.Container(
            content=ft.Text("RECORDING", size=17, weight=ft.FontWeight.W_900, color=ACCENT),
            bgcolor=CARD, border_radius=10,
            padding=ft.Padding(10, 8, 10, 8), border=_border(1, BORDER))
        rec_card = ft.Container(
            content=ft.Row([
                rec_title_bar,
                self.rec_btn, self.stop_btn, self.rec_info,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=CARD2, border_radius=14, padding=10,
            border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, spread_radius=0,
                                color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        # Sequence — scrollable, fills the right column height
        self.seq_col = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
        self.empty_lbl = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.LAYERS_OUTLINED, color=EMPTY_ICON, size=18),
                ft.Text("NO TAKES YET — PRESS REC THEN STOP",
                        color=EMPTY_TEXT, size=15, weight=ft.FontWeight.W_800),
            ], spacing=8),
            bgcolor=EMPTY_BG,
            border_radius=10, padding=ft.Padding(14, 12, 14, 12),
            border=_border(1, EMPTY_BORDER))
        self.seq_col.controls.append(self.empty_lbl)
        actions = ft.Row([
            _pill("▶  Play",      self._play_selected,         ACCENT,  expand=True),
            _pill("◀  Reverse",   self._play_selected_rev,     TEAL,    expand=True),
            _pill("⊕  Combine",   self._combine_selected,      INDIGO,  expand=True),
            _pill("⊘  Uncombine", self._uncombine_selected,    PURPLE,  expand=True),
            _pill("✕  Delete",    self._delete_selected,       DANGER,  expand=True),
            _pill("Clear",        self._clear_all,             NEUTRAL, expand=True),
        ], spacing=6)
        seq_card = ft.Container(
            content=ft.Column([
                ft.Text("SEQUENCE  ·  PLAYS TOP → BOTTOM", size=17,
                        weight=ft.FontWeight.W_900, color=ACCENT),
                ft.Container(self.seq_col, expand=True),  # scrollable list fills height
                actions,
            ], spacing=10, expand=True),
            bgcolor=CARD2, border_radius=16, padding=14, border=_border(1, BORDER),
            expand=True,
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        # Smoothing
        self._smooth_ref = [self.smooth_mode]
        smooth_tog = _toggle_group(["None", "Linear", "EMA"], self._smooth_ref,
                                   lambda v: setattr(self, "smooth_mode", v),
                                   size=12, height=32)
        self.smooth_val = ft.Text("0.50", size=16, color=FG_VAL,
                                  weight=ft.FontWeight.W_900, width=52)
        smooth_slider = ft.Slider(min=0.0, max=1.0, value=0.5, expand=True,
                                  active_color=SLIDER_ACTIVE, thumb_color=SLIDER_THUMB,
                                  on_change=self._on_smooth_strength)
        smooth_card = _card("Auto-smooth  ·  applied on Stop",
                            ft.Row([smooth_tog,
                                    ft.Text("STRENGTH", color=FG_DIM, size=15,
                                            weight=ft.FontWeight.W_900),
                                    smooth_slider, self.smooth_val],
                                   spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        # Save preset — inline dropdown + add-custom + action buttons
        self.speed_mult = 1.0
        self.speed_val  = ft.Text("×1.00", size=16, color=FG_VAL,
                                  weight=ft.FontWeight.W_900, width=62)
        speed_slider = ft.Slider(min=0.25, max=4.0, value=1.0, expand=True,
                                 active_color=SPEED_ACTIVE, thumb_color=SPEED_THUMB,
                                 on_change=self._on_speed)

        self._gesture_items: list[str] = list(GESTURES)
        # Scrollable preset list (fills the bottom-left gap, like the takes list)
        self.preset_list = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

        self._custom_gesture_field = ft.TextField(
            hint_text="NEW PRESET NAME…", dense=True, filled=True,
            bgcolor=CARD2, border_radius=10, border_color=BORDER,
            content_padding=ft.Padding(10, 6, 10, 6),
            text_size=15, color=FG, text_style=ft.TextStyle(weight=ft.FontWeight.W_700),
            hint_style=ft.TextStyle(color=FG_DIM, weight=ft.FontWeight.W_600),
            expand=True)

        save_card = ft.Container(
            content=ft.Column([
                ft.Text("PRESET", color=ACCENT, size=17, weight=ft.FontWeight.W_900),
                ft.Container(self.preset_list, expand=True),  # scrollable list fills gap
                ft.Row([self._custom_gesture_field,
                        _pill("+ Add", self._add_gesture, ACCENT, height=36, width=110),
                        _pill("✕ Remove", self._remove_gesture, DANGER, height=36, width=150)],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([
                    _pill("Save Preset", self._save_preset,         ACCENT,  height=34, expand=True),
                    _pill("▶ Play",      self._play_from_final,     NEUTRAL, height=34, expand=True),
                    _pill("◀ Reverse",   self._play_from_final_rev, NEUTRAL, height=34, expand=True),
                ], spacing=6),
                ft.Row([ft.Text("SPEED", color=FG_DIM, size=15, weight=ft.FontWeight.W_900),
                        speed_slider, self.speed_val,
                        _pill("Save @ Speed", self._save_at_speed, PURPLE, height=34)],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=10, expand=True),
            bgcolor=CARD2, border_radius=16, padding=14, border=_border(1, BORDER),
            expand=True,
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        self.status = ft.Text("", size=12, color=FG_DIM, weight=ft.FontWeight.W_600)

        # Two-column layout: LEFT = hands + smoothing + preset/speed,
        #                    RIGHT = recording + scrollable takes list (full height)
        left_col = ft.Column(
            [hands, smooth_card, ft.Container(save_card, expand=True)],
            spacing=12, expand=True)
        right_col = ft.Column(
            [rec_card, seq_card],
            spacing=12, expand=True)

        main_row = ft.Row(
            [ft.Container(left_col, expand=True),
             ft.Container(right_col, expand=True)],
            spacing=14, expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH)

        title_card = ft.Container(
            content=ft.Text("INSPIRE ROBOTICS HANDS  ·  ANIMATION FRAMES RECORDER",
                            size=24, weight=ft.FontWeight.W_900, color=ACCENT),
            bgcolor=CARD2, border_radius=16,
            padding=ft.Padding(18, 14, 18, 14), border=_border(1, BORDER),
            shadow=ft.BoxShadow(blur_radius=16, color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

        inner = ft.Column([
            title_card,
            main_row,
        ], spacing=12, expand=True,
           horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

        p.add(ft.Container(inner, padding=ft.Padding(16, 14, 16, 14), expand=True))
        self._rebuild_seq()
        self._rebuild_preset_list()

    def _hand_card(self, side: str) -> ft.Container:
        ch = self.channels[side]
        ok = ch.hand is not None
        conn = ft.Text("● CONNECTED" if ok else f"○ {(ch.error or 'NOT FOUND').upper()}",
                       size=13, color=OK if ok else ERR, weight=ft.FontWeight.W_900)
        self.conn_lbls[side] = conn

        rows = []
        self.sliders[side] = []
        self.val_lbls[side] = []
        JOINT_LABELS = [
            "PINKY - BEND", "RING - BEND", "MIDDLE - BEND", "INDEX - BEND",
            "THUMB - BEND", "THUMB - ROT",
        ]
        for i, name in enumerate(JOINT_NAMES):
            label = JOINT_LABELS[i]
            val = ft.Text(str(SCALE_MAX), size=16, color=FG_VAL,
                          weight=ft.FontWeight.W_900, width=48,
                          text_align=ft.TextAlign.CENTER)
            sld = ft.Slider(min=SCALE_MIN, max=SCALE_MAX, value=SCALE_MAX, expand=True,
                            active_color=SLIDER_ACTIVE, thumb_color=SLIDER_THUMB,
                            on_change=lambda e, s=side, idx=i: self._on_slider(s, idx, e))
            self.sliders[side].append(sld)
            self.val_lbls[side].append(val)
            rows.append(ft.Row(
                [ft.Text(label, size=15, color=LBL, width=124,
                         weight=ft.FontWeight.W_900), sld, val],
                spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))

        btns = ft.Row([
            _pill("Open",      lambda e, s=side: self._open(s),      NEUTRAL, expand=True, height=30, size=12),
            _pill("Mirror →",  lambda e, s=side: self._mirror(s),    GREEN,   expand=True, height=30, size=12),
            _pill("Reconnect", lambda e, s=side: self._reconnect(s), ACCENT,  expand=True, height=30, size=12),
        ], spacing=4)

        title_bar = ft.Container(
            content=ft.Row([
                ft.Text(side.upper(), size=20, weight=ft.FontWeight.W_900, color=ACCENT),
                ft.Container(self._port_dd(side), expand=True),
                conn,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=CARD,             # slightly lighter than the card body to stand out
            border_radius=10, padding=ft.Padding(10, 8, 10, 8),
            border=_border(1, BORDER))

        return ft.Container(
            content=ft.Column([
                title_bar,
                *rows,
                ft.Container(btns, padding=ft.Padding(0, 4, 0, 0)),
            ], spacing=6),
            bgcolor=CARD2, border_radius=14, padding=10,
            border=_border(1, BORDER), expand=True,
            shadow=ft.BoxShadow(blur_radius=16, spread_radius=0,
                                color=ft.Colors.with_opacity(0.45, "#0a1830"),
                                offset=ft.Offset(0, 6)))

    # ------------------------------------------------------------------
    # Sequence list rendering
    # ------------------------------------------------------------------

    def _rebuild_seq(self):
        self.seq_col.controls.clear()
        if not self.sequence:
            self.seq_col.controls.append(self.empty_lbl)
            self._safe_update()
            return
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
        end = ft.TextField(value=str(len(item.take.frames)), dense=True, filled=True,
                           bgcolor=CARD, border_radius=10, width=64,
                           border_color="transparent", content_padding=10,
                           text_size=12, color=FG,
                           on_submit=lambda e, it=item, lb=info: self._apply_end(it, e.control, lb))

        # Drag handle icon (shows the row is draggable)
        drag_handle = ft.Icon(ft.Icons.DRAG_HANDLE, color=FG_DIM, size=20)

        row_content = ft.Container(
            content=ft.Row([drag_handle, *left, name, info,
                            ft.Text("end", color=FG_DIM, size=12), end,
                            ft.Container(expand=True)],
                           spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ITEM, border_radius=12, border=_border(1, BORDER),
            padding=ft.Padding(10, 6, 8, 6))

        # Ghost: full-width row clone, translucent, floating while dragging
        ghost = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.DRAG_HANDLE, color=FG, size=20),
                ft.Text(item.take.name, color=FG, size=15, weight=ft.FontWeight.W_900),
                ft.Text(f"  {item.info}", color=FG_DIM, size=13),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=ACCENT, border_radius=12,
            padding=ft.Padding(14, 10, 14, 10),
            opacity=0.75, width=600)

        draggable = ft.Draggable(
            group="takes", content=row_content,
            content_when_dragging=ft.Container(
                height=6, bgcolor=ACCENT, border_radius=3, opacity=0.4),
            content_feedback=ghost,
            data=idx)

        # Drop target highlights and swaps on accept
        target_ref = ft.Ref[ft.Container]()

        def _on_will_accept(e, ref=target_ref):
            ref.current.border = _border(2, ACCENT)
            ref.current.update()

        def _on_leave(e, ref=target_ref):
            ref.current.border = _border(1, BORDER)
            ref.current.update()

        def _on_accept(e, to_idx=idx, ref=target_ref):
            # e.src is the Draggable; e.src.data holds the from-index
            try:
                from_idx = int(e.src.data) if e.src and e.src.data is not None else None
            except (TypeError, ValueError):
                from_idx = None
            ref.current.border = _border(1, BORDER)
            if from_idx is not None and from_idx != to_idx:
                self.sequence.insert(to_idx, self.sequence.pop(from_idx))
                self._rebuild_seq()

        wrapper = ft.Container(
            ref=target_ref, content=ft.DragTarget(
                group="takes", content=draggable,
                on_will_accept=_on_will_accept,
                on_leave=_on_leave,
                on_accept=_on_accept),
            border_radius=12, border=_border(1, BORDER))

        return wrapper

    def _toggle_expand(self, item: SeqItem):
        item._ui_expanded = not item._ui_expanded
        self._rebuild_seq()

    def _apply_end(self, item: SeqItem, ctrl, info_lbl):
        try:
            new_end = int(ctrl.value)
        except ValueError:
            ctrl.value = str(len(item.take.frames)); self._safe_update(); return
        new_end = max(2, min(new_end, len(item.take.frames)))
        item.take.frames = item.take.frames[:new_end]
        ctrl.value = str(len(item.take.frames))
        info_lbl.value = item.info
        self._set_status(f"'{item.take.name}' trimmed to {new_end} frames")

    def _selected(self) -> list[int]:
        return [i for i, it in enumerate(self.sequence) if it._ui_checked]

    def _move(self, i: int, d: int):
        j = i + d
        if 0 <= j < len(self.sequence):
            self.sequence[i], self.sequence[j] = self.sequence[j], self.sequence[i]
            self._rebuild_seq()

    # ------------------------------------------------------------------
    # Send / record / playback loop (daemon thread, 40 ms)
    # ------------------------------------------------------------------

    async def _loop_async(self):
        """Tick loop on Flet's own event loop — updates flush synchronously
        (equivalent to tkinter's after(40)), so sliders animate in real time."""
        import asyncio
        last_scan = 0.0
        while True:
            now = time.monotonic()
            for side, ch in self.channels.items():
                ch.send(list(self.slider_vals[side]))

            # Re-scan serial ports every ~1.5s for hotplug (dynamic switching)
            if now - last_scan > 1.5:
                last_scan = now
                self._refresh_ports()

            dirty = False
            if self._recording and (now - self._rec_last_t) >= REC_DT:
                self._rec_frames.append(
                    {s: list(self.slider_vals[s]) for s in self.channels})
                self._rec_last_t = now
                self.rec_info.value = f"{len(self._rec_frames)} frames · {now - self._rec_start:.1f}s"
                dirty = True

            if self._playing and self._play_segments:
                mult = self.speed_mult
                seg = self._play_segments[self._play_seg_idx]
                if self._play_dwell_until > 0.0:
                    self._render(seg[-1])
                    if now >= self._play_dwell_until:
                        self._play_dwell_until = 0.0
                        self._play_seg_idx += 1
                        if self._play_seg_idx >= len(self._play_segments):
                            self._playing = False
                            self.status.value = "Playback done"
                        else:
                            self._play_start_t = now
                    dirty = True
                else:
                    elapsed = now - self._play_start_t
                    fi = int(elapsed * self._play_fps * mult)
                    if fi >= len(seg) - 1:
                        self._render(seg[-1])
                        self._play_dwell_until = now + SEGMENT_SETTLE_SEC
                    else:
                        self._render(seg[fi])
                    dirty = True

            if dirty:
                self._safe_update()
            await asyncio.sleep(0.04 if not self._playing else 0.02)

    def _render(self, f: dict[str, list[int]]):
        for side, angles in f.items():
            if side in self.channels:
                self.channels[side].send(angles, immediate=True)
                self.slider_vals[side] = list(angles)
                for d, v in enumerate(angles):
                    self.sliders[side][d].value = v
                    self.val_lbls[side][d].value = str(v)

    def _start_segments(self, segments, fps, status):
        segments = [s for s in segments if s]
        if not segments:
            return
        for ch in self.channels.values():
            ch.set_motor_speed(min(1000, int(800 * max(1.0, self.speed_mult))))
        self._play_segments = segments
        self._play_seg_idx = 0
        self._play_fps = fps
        self._play_dwell_until = 0.0
        self._playing = True
        self._play_start_t = time.monotonic()
        self._set_status(status)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_slider(self, side, idx, e):
        v = int(e.control.value)
        self.slider_vals[side][idx] = v
        self.val_lbls[side][idx].value = str(v)
        self.val_lbls[side][idx].update()

    def _rebuild_preset_list(self):
        """Render the scrollable preset list; highlight the selected one."""
        if self.gesture not in self._gesture_items and self._gesture_items:
            self.gesture = self._gesture_items[0]
        self.preset_list.controls.clear()
        for name in self._gesture_items:
            sel  = name == self.gesture
            perm = name in PERMANENT
            def _click(e, v=name):
                self.gesture = v
                self._rebuild_preset_list()
                self._safe_update()
            row = ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.LOCK if perm else ft.Icons.LABEL_OUTLINE,
                            size=16, color=(BG if sel else FG_DIM)),
                    ft.Text(name, size=15, weight=ft.FontWeight.W_900,
                            color=(BG if sel else FG)),
                ], spacing=8),
                on_click=_click,
                bgcolor=ACCENT if sel else ITEM,
                border_radius=10, padding=ft.Padding(12, 8, 12, 8),
                border=_border(1, BORDER))
            self.preset_list.controls.append(row)
        self._safe_update()

    def _add_gesture(self, e=None):
        name = self._custom_gesture_field.value.strip().replace(" ", "_").lower()
        if not name:
            return
        if not self.sequence:
            self._set_status("Add some takes before creating a preset."); return
        if name not in self._gesture_items:
            self._gesture_items.append(name)
        self.gesture = name
        self._custom_gesture_field.value = ""
        self._rebuild_preset_list()
        self._safe_update()

    def _remove_gesture(self, e=None):
        name = self.gesture
        if name in PERMANENT:
            self._set_status(f"'{name}' is permanent — cannot remove"); return
        self._show_confirm_dialog(
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

    def _show_info_dialog(self, title: str, body: str):
        """Show a modal info popup with a single OK button."""
        def _ok(e):
            self.page.pop_dialog()
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(title, size=16, weight=ft.FontWeight.W_900, color=FG_VAL),
            content=ft.Text(body, size=13, color=FG_DIM),
            bgcolor=CARD2,
            actions=[ft.TextButton("OK", on_click=_ok,
                                   style=ft.ButtonStyle(color=ACCENT))],
            actions_alignment=ft.MainAxisAlignment.END)
        self.page.show_dialog(dlg)

    def _show_confirm_dialog(self, title: str, body: str, on_confirm):
        """Show a modal confirm/cancel dialog."""
        def _yes(e):
            self.page.pop_dialog()
            on_confirm()
        def _no(e):
            self.page.pop_dialog()
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(title, size=16, weight=ft.FontWeight.W_900, color=FG),
            content=ft.Text(body, size=13, color=FG_DIM),
            bgcolor=CARD2,
            actions=[
                ft.TextButton("CANCEL", on_click=_no,
                              style=ft.ButtonStyle(color=FG_DIM)),
                ft.TextButton("DELETE", on_click=_yes,
                              style=ft.ButtonStyle(color=DANGER)),
            ],
            actions_alignment=ft.MainAxisAlignment.END)
        self.page.show_dialog(dlg)

    def _on_smooth_strength(self, e):
        self.smooth_strength = float(e.control.value)
        self.smooth_val.value = f"{self.smooth_strength:.2f}"
        self.smooth_val.update()

    def _on_speed(self, e):
        self.speed_mult = float(e.control.value)
        self.speed_val.value = f"×{self.speed_mult:.2f}"
        self.speed_val.update()

    def _open(self, side):
        for d in range(6):
            self.slider_vals[side][d] = SCALE_MAX
            self.sliders[side][d].value = SCALE_MAX
            self.val_lbls[side][d].value = str(SCALE_MAX)
        self._set_status(f"{side} → open (all 1000)")

    def _mirror(self, source):
        target = "right" if source == "left" else "left"
        for d in range(6):
            v = self.slider_vals[source][d]
            if d == 5:
                v = SCALE_MAX - v
            self.slider_vals[target][d] = v
            self.sliders[target][d].value = v
            self.val_lbls[target][d].value = str(v)
        self._set_status(f"mirrored {source} → {target}")

    def _reconnect(self, side):
        ch = self.channels[side]
        ok = ch.reconnect()
        self._set_conn(side, ok, ch)
        self._set_status(f"{side} {'reconnected' if ok else 'failed'}")

    def _set_conn(self, side, ok, ch):
        lbl = self.conn_lbls[side]
        lbl.value = "● CONNECTED" if ok else f"○ {(ch.error or 'NOT FOUND').upper()}"
        lbl.color = OK if ok else ERR

    # ------------------------------------------------------------------
    # Serial port selection
    # ------------------------------------------------------------------

    def _resolve_default_ports(self):
        """Pick (left_device, right_device) — saved mapping by serial number
        if the devices are still present, else the first two available ports."""
        ports = self._ports
        by_sn = {p["sn"]: p["device"] for p in ports if p["sn"]}
        devices = [p["device"] for p in ports]
        saved = _load_serial_map()

        def pick(side, fallback_idx):
            sn = saved.get(f"{side}_sn")
            if sn and sn in by_sn:
                return by_sn[sn]
            dev = saved.get(f"{side}_dev")
            if dev and dev in devices:
                return dev
            return devices[fallback_idx] if fallback_idx < len(devices) else SIDE_PORTS[side]

        left  = pick("left", 0)
        right = pick("right", 1)
        if left == right and len(devices) > 1:   # avoid both on same port
            right = next((d for d in devices if d != left), right)
        return left, right

    def _save_port_mapping(self):
        by_dev = {p["device"]: p["sn"] for p in self._ports}
        mapping = {}
        for side in ("left", "right"):
            dev = self.channels[side].port
            mapping[f"{side}_dev"] = dev
            mapping[f"{side}_sn"]  = by_dev.get(dev, "")
        _save_serial_map(mapping)

    def _port_dd(self, side) -> ft.Dropdown:
        dd = ft.Dropdown(
            value=self.channels[side].port, expand=True, dense=True,
            bgcolor=CARD2, color=FG, border_color=BORDER, border_radius=8,
            text_size=12, text_style=ft.TextStyle(weight=ft.FontWeight.W_700, color=FG),
            options=[ft.dropdown.Option(p["device"]) for p in self._ports],
            on_select=lambda e, s=side: self._on_port_select(s, e.control.value))
        self.port_dds[side] = dd
        return dd

    def _on_port_select(self, side, device):
        if not device:
            return
        ok = self.channels[side].set_port(device)
        self._set_conn(side, ok, self.channels[side])
        self._save_port_mapping()
        self._set_status(f"{side} → {device} ({'ok' if ok else 'failed'})")
        self._safe_update()

    def _refresh_ports(self):
        """Re-scan ports; update dropdown options if the device set changed."""
        ports = scan_serial_ports()
        devs = [p["device"] for p in ports]
        if devs == self._known_devices:
            return
        self._ports = ports
        self._known_devices = devs
        for side, dd in self.port_dds.items():
            dd.options = [ft.dropdown.Option(p["device"]) for p in ports]
            if self.channels[side].port not in devs and devs:
                self.channels[side].set_port(devs[0])
                dd.value = devs[0]
        self._safe_update()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def _start_rec(self, e=None):
        if self._recording:
            return
        self._rec_frames = []
        self._rec_start = self._rec_last_t = time.monotonic()
        self._recording = True
        self.rec_btn.disabled = True
        self.stop_btn.disabled = False
        self._set_status("Recording…")

    def _stop_rec(self, e=None):
        if not self._recording:
            return
        self._recording = False
        self.rec_btn.disabled = False
        self.stop_btn.disabled = True
        self.rec_info.value = "0 FRAMES · 0.0s"   # reset counter
        raw = list(self._rec_frames)
        if not raw:
            self._show_info_dialog("Empty Take", "Nothing was recorded."); return
        frames = _trim_static(raw)
        if not frames:
            self._show_info_dialog("Empty Take", "No movement detected — take discarded."); return
        # Discard if total motion across all joints < threshold (nearly static)
        def _total_motion(fs):
            total = 0
            for s in ("left", "right"):
                vals = [f.get(s, [1000]*6) for f in fs]
                for j in range(6):
                    col = [v[j] for v in vals]
                    total += max(col) - min(col)
            return total
        if _total_motion(frames) < MOVE_THRESHOLD * 3:
            self._show_info_dialog("Empty Take",
                                   "Sliders barely moved — take discarded.\n"
                                   "Move at least one joint further to record."); return
        mode, strength = self.smooth_mode, self.smooth_strength
        n_raw = len(frames)
        if mode == "Linear":
            frames = _smooth_linear(frames, max(2, int(40 - strength * 38)))
        elif mode == "EMA":
            a = max(0.05, 0.9 - strength * 0.85)
            frames = _smooth_ema(_smooth_ema(frames, a), a)
        take = Take(frames, REC_HZ)
        self.sequence.append(SeqItem(take))
        self.rec_info.value = "0 frames · 0.0s"
        note = f"  [{mode} {strength:.2f}]" if mode != "None" else ""
        self._set_status(f"Saved '{take.name}': {n_raw}→{len(frames)} fr, {take.duration:.1f}s{note}")
        self._rebuild_seq()

    # ------------------------------------------------------------------
    # Sequence actions
    # ------------------------------------------------------------------

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
        group = SeqItem(_parallel_merge(children), children)
        first = sel[0]
        for i in sorted(sel, reverse=True):
            self.sequence.pop(i)
        self.sequence.insert(first, group)
        self._set_status(f"Combined {len(children)} tracks → {len(group.take.frames)} fr")
        self._rebuild_seq()

    def _uncombine_selected(self, e=None):
        """Split a combined group back into its original child takes.
        Discards the averaged/merged frame data — raw children are restored."""
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

    # ------------------------------------------------------------------
    # Save / preview preset
    # ------------------------------------------------------------------

    def _save_preset(self, e=None, speed: float = 1.0):
        if not self.sequence:
            self._set_status("Add some takes first."); return
        dest = PRESETS_DIR / self.gesture
        dest.mkdir(parents=True, exist_ok=True)
        frames = _even_speed(_compose_sequence([it.take for it in self.sequence]))
        sides = set()
        for f in frames:
            sides.update(f.keys())
        out_fps = round(REC_HZ * speed)
        data = {"gesture": self.gesture, "fps": out_fps,
                "frames": {s: [f.get(s, [0]*6) for f in frames] for s in sorted(sides)}}
        (dest / "animation.yaml").write_text(
            yaml.dump(data, default_flow_style=False, sort_keys=False))
        note = f" @ ×{speed:.2f} (fps={out_fps})" if speed != 1.0 else ""
        self._set_status(f"Saved '{self.gesture}': {len(frames)} fr, {len(frames)/out_fps:.1f}s{note}")

    def _save_at_speed(self, e=None):
        self._save_preset(speed=self.speed_mult)

    def _play_preset(self, reverse: bool):
        path = PRESETS_DIR / self.gesture / "animation.yaml"
        if not path.exists():
            self._set_status(f"No animation.yaml for '{self.gesture}'."); return
        self._play_yaml(path, reverse)

    def _play_from_final(self, e=None):
        self._play_gesture(reverse=False)

    def _play_from_final_rev(self, e=None):
        self._play_gesture(reverse=True)

    def _play_gesture(self, reverse: bool):
        """Play the selected gesture: prefer final_saves, fall back to presets."""
        final = PROJECT_ROOT / "final_saves" / self.gesture / "animation.yaml"
        preset = PRESETS_DIR / self.gesture / "animation.yaml"
        if final.exists():
            self._play_yaml(final, reverse)
        elif preset.exists():
            self._play_yaml(preset, reverse)
        else:
            self._show_info_dialog(
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
        frames = [{s: raw[s][min(i, len(raw[s])-1)] for s in sides} for i in range(n)]
        if reverse:
            frames = list(reversed(frames))
        src = path.parent.parent.name   # "final_saves" or "presets"
        d = "rev" if reverse else "fwd"
        self._start_segments([frames], fps,
                             f"Playing {src}/{self.gesture} ({d}): {n} fr, {n/fps:.1f}s")

    # ------------------------------------------------------------------

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
        for ch in self.channels.values():
            ch.close()


# ---------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description="Hand animation recorder")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--dry-run", action="store_true", help="UI only, no hardware")
    ap.add_argument("--theme", choices=list(THEMES), default="blue",
                    help="colour theme: blue (default) or bw (black & white)")
    args, _ = ap.parse_known_args()

    _apply_theme(args.theme)

    if args.dry_run:
        HandChannel._try_connect = lambda self: False  # type: ignore[method-assign]

    def target(page: ft.Page):
        app = RecorderApp(page, baud=args.baud)
        page.on_close = lambda e: app.close()

    ft.run(target)


if __name__ == "__main__":
    main()
