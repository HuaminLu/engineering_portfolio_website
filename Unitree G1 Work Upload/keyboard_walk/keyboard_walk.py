#!/usr/bin/env python3
"""keyboard_walk.py — compact Flet GUI to walk the Unitree G1 with the keyboard.

Clones the tele-op drive logic from sentdex's ``run_geoff_gui.py`` into a small
standalone window: press W/A/S/D/Q/E to walk, the background loop ramps the
velocity and streams ``LocoClient.Move(vx, vy, omega)`` to the robot at ~15 Hz.

Key map (same as run_geoff_gui)::

        Q   W   E            W / S : forward / back      (vx)
        A   S   D            A / D : rotate left / right (omega)
                             Q / E : strafe left / right (vy)
    SPACE = stop     SHIFT = fast     Z = damp+quit     ESC = stop+quit

You can also click-and-hold the on-screen key boxes (mouse) — handy because Flet
only reports key *presses*, not releases, so held keys are tracked with a short
timeout while the mouse boxes give precise press/release.

Safety: the drive loop does **nothing** until you flip **ENABLE** on. SPACE
always forces an immediate stop. ESC stops + zero-torque; Z damps.

Usage
=====
    # real robot (must be on the G1 network — see --iface)
    python3 keyboard_walk.py --iface enxa0cec8b8657b

    # UI only, no robot / no DDS
    python3 keyboard_walk.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
import threading as _threading
import time
from pathlib import Path

import flet as ft

# Best-effort: fall back to the SDK vendored in the sibling sentdex project so
# this runs out-of-the-box on the workstation without a separate pip install.
_SENTDEX_SDK = (Path(__file__).resolve().parent.parent
                / "arm_mimic" / "sentdex" / "unitree_sdk2_python")
if _SENTDEX_SDK.exists():
    sys.path.insert(0, str(_SENTDEX_SDK))

# ---------------------------------------------------------------------------
# Theme (compact, matches the arm recorder's dark look)
# ---------------------------------------------------------------------------
BG      = "#0f0f0f"
CARD    = "#1a1a1a"
CARD2   = "#212121"
BORDER  = "#333333"
FG      = "#e8e8e8"
FG_DIM  = "#8a8a8a"
FG_VAL  = "#ffffff"
ACCENT  = "#f5c800"
OK      = "#34d399"
ERR     = "#f87171"
TEAL    = "#38bdf8"
DANGER  = "#ef4444"
KEY_IDLE = "#2a2a2a"
TURQUOISE = "#2dd4bf"    # press-feedback highlight (matches the teal theme accent)
KEY_HOT  = TURQUOISE

# ---------------------------------------------------------------------------
# Drive tuning (from run_geoff_gui.py)
# ---------------------------------------------------------------------------
TICK_HZ = 15.0
TICK_DT = 1.0 / TICK_HZ
HOLD_TIMEOUT = 0.35      # s — a keyboard key counts as "held" this long after
                         # its last (auto-repeat) event, since Flet has no keyup

# Each tap fires one bounded velocity pulse, then STOP. Tapping again re-fires the
# same fixed pulse — speed never accumulates. The SPEED slider scales the pulse
# magnitude: low end = small precise nudge, high end = a big fast lunge (further,
# because distance = speed x PULSE_SECS). Range mirrors the joystick's velocity band.
SPEED_MIN = 0.15         # m/s  linear pulse velocity at the slider minimum
SPEED_MAX = 1.20         # m/s  linear pulse velocity at the slider maximum
SPEED_DEFAULT = 0.30     # m/s  starting value (matches the old fixed small step)
ANG_RATIO = 1.25         # rad/s of turn per 1 m/s of linear speed (slider scales both)
PULSE_SECS = 0.45        # how long one tap drives before it auto-stops
PULSE_TICKS = max(1, round(PULSE_SECS / TICK_DT))
PULSE_FAST = 1.6         # SHIFT boost multiplier on top of the slider
VMAX_LIN = 1.4           # m/s  hard safety clamp (linear)
VMAX_ANG = 2.0           # rad/s hard safety clamp (angular)


class _NoiseFilter:
    """Line-buffered stdout wrapper that drops known-harmless vendored-SDK
    chatter. The lease-renewal thread prints ``[LeaseClient] apply lease error
    3102`` and ``[ClientStub] send request error`` every ~second; on this
    firmware the loco commands don't need a held lease (they return code=0 and
    the robot moves anyway), so these lines are noise, not failures. Filtering
    is purely cosmetic — it never touches what's sent to the robot."""

    _DROP = ("[LeaseClient]", "[ClientStub]")

    def __init__(self, stream):
        self._s = stream
        self._buf = ""
        self._lock = _threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            self._buf += text
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if not any(tok in line for tok in self._DROP):
                    self._s.write(line + "\n")
        return len(text)

    def flush(self):
        self._s.flush()

    def __getattr__(self, name):
        return getattr(self._s, name)


def _install_noise_filter() -> None:
    """Replace sys.stdout with a filtered wrapper (idempotent)."""
    if not isinstance(sys.stdout, _NoiseFilter):
        sys.stdout = _NoiseFilter(sys.stdout)


def _clamp(v: float, limit: float) -> float:
    return max(-limit, min(limit, v))


def _border(w: int = 1, color: str = BORDER):
    s = ft.BorderSide(w, color)
    return ft.Border(top=s, right=s, bottom=s, left=s)


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


def _resolve_g1_iface(preferred: str | None = None) -> str:
    """Return the NIC currently holding a 192.168.123.x address (the G1 wired
    bus) so the command works no matter which dongle / interface name is in use.

    Prefers ``preferred`` if it's already on the bus; otherwise auto-picks the
    first wired NIC with a 192.168.123.x address. Falls back to ``preferred``.
    """
    import os
    try:
        names = [n for n in os.listdir("/sys/class/net") if n != "lo"]
    except OSError:
        names = []

    def on_bus(n: str) -> bool:
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
# Locomotion backend — LocoClient wrapper (canonical g1_loco_client pattern)
# ---------------------------------------------------------------------------
class LocoBackend:
    def __init__(self, iface: str, dry_run: bool = False, loco_service: str = "sport"):
        self.iface = iface
        self.dry_run = dry_run
        self.loco_service = loco_service
        self.bot = None
        self.ready = False
        self.error: str | None = None
        self._bal_mode = -1
        self.last_rpc: int | None = None
        if not dry_run:
            self._init()

    def _init(self) -> None:
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize  # type: ignore
            # The loco RPC service name depends on firmware: ai_sport >= 8.2.0.0
            # exposes it as "sport"; older firmware used "loco". The vendored SDK
            # hard-codes "loco", so we override the module constant BEFORE the
            # client is constructed (the name is read in LocoClient.__init__).
            # Wrong name => LeaseClient can't reach the service => every RPC fails
            # with code 3102 (RPC_ERR_CLIENT_SEND) and the robot never moves.
            import unitree_sdk2py.g1.loco.g1_loco_api as _loco_api      # type: ignore
            import unitree_sdk2py.g1.loco.g1_loco_client as _loco_cli   # type: ignore
            _loco_api.LOCO_SERVICE_NAME = self.loco_service
            _loco_cli.LOCO_SERVICE_NAME = self.loco_service
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient  # type: ignore
        except Exception as exc:
            self.error = f"SDK import failed: {exc}"
            print(f"[keyboard_walk] {self.error}", file=sys.stderr)
            return
        try:
            ChannelFactoryInitialize(0, self.iface)
            bot = LocoClient()
            bot.SetTimeout(10.0)
            bot.Init()
            self.bot = bot
            self.ready = True
            print(f"[keyboard_walk] LocoClient ready on {self.iface}")
        except Exception as exc:
            self.error = f"LocoClient init failed: {exc}"
            print(f"[keyboard_walk] {self.error}", file=sys.stderr)

    def start_walk(self) -> int | None:
        """Engage the walking gait controller (FSM 200) so Move commands take
        effect. Returns the RPC code (0 = OK; non-zero usually means the loco
        lease is held elsewhere — see the 3102 note)."""
        if self.bot is None:
            return None
        try:
            code = self.bot.SetFsmId(200)   # == LocoClient.Start()
            try:
                self.bot.SetBalanceMode(1)  # continuous gait, set ONCE (not per tick)
            except Exception as bexc:
                print(f"[keyboard_walk] SetBalanceMode(1) raised: {bexc}", flush=True)
            if code == 0:
                print("[keyboard_walk] gait engaged (drive live)", flush=True)
            else:
                print(f"[keyboard_walk] engage rejected: code={code}", flush=True)
            self.last_rpc = code
            return code
        except Exception as exc:
            self.error = f"Start failed: {exc}"
            print(f"[keyboard_walk] engage raised: {exc}", flush=True)
            return -1

    def move(self, vx: float, vy: float, omega: float) -> int | None:
        """Stream a body-velocity setpoint. Returns the RPC code so the UI can
        flag lease/authority rejections instead of silently doing nothing."""
        if self.bot is None:
            return None
        try:
            # SetVelocity returns the RPC code; Move() wraps it but drops the
            # code, so call SetVelocity directly with a long duration (persistent).
            code = self.bot.SetVelocity(vx, vy, omega, 864000.0)
            self.last_rpc = code
            return code
        except Exception as exc:
            print(f"[keyboard_walk] Move failed: {exc}", file=sys.stderr)
            self.last_rpc = -1
            return -1

    def stop(self) -> None:
        if self.bot is not None:
            try:
                self.bot.StopMove()
            except Exception:
                pass

    def damp(self) -> None:
        if self.bot is not None:
            try:
                self.bot.Damp()
            except Exception:
                pass

    def zero_torque(self) -> None:
        if self.bot is not None:
            try:
                self.bot.ZeroTorque()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
# key -> (axis, sign, label, sublabel)
DRIVE_KEYS = {
    "w": ("vx", +1, "W", "FWD"),
    "s": ("vx", -1, "S", "BACK"),
    "a": ("omega", +1, "A", "TURN L"),
    "d": ("omega", -1, "D", "TURN R"),
    "q": ("vy", +1, "Q", "STRAFE L"),
    "e": ("vy", -1, "E", "STRAFE R"),
}


class KeyboardWalkApp:
    def __init__(self, page: ft.Page, iface: str, dry_run: bool, loco_service: str = "sport"):
        self.page = page
        self.iface = iface
        self.back = LocoBackend(iface, dry_run=dry_run, loco_service=loco_service)

        self.vx = self.vy = self.omega = 0.0
        self._last_key: dict[str, float] = {}   # keyboard auto-repeat timestamps
        self._mouse_held: set[str] = set()       # boxes pressed with the mouse
        self._shift = False
        self.enabled = False
        self.speed = SPEED_DEFAULT   # linear pulse velocity (m/s), set by the slider
        # discrete tap pulses: per-axis remaining ticks + velocity to hold
        self._pulse_ticks = {"vx": 0, "vy": 0, "omega": 0}
        self._pulse_vel = {"vx": 0.0, "vy": 0.0, "omega": 0.0}

        self.key_boxes: dict[str, ft.Container] = {}
        self._build()
        self.page.run_task(self._loop)

    # ---- key state ----------------------------------------------------- #
    def _active(self, k: str) -> bool:
        if k in self._mouse_held:
            return True
        t = self._last_key.get(k)
        return t is not None and (time.monotonic() - t) < HOLD_TIMEOUT

    # ---- UI ------------------------------------------------------------ #
    def _key_box(self, k: str) -> ft.Container:
        _, _, label, sub = DRIVE_KEYS[k]
        box = ft.Container(
            content=ft.Column([
                ft.Text(label, size=22, weight=ft.FontWeight.W_900, color=FG),
                ft.Text(sub, size=9, weight=ft.FontWeight.W_800, color=FG_DIM),
            ], spacing=0, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.MainAxisAlignment.CENTER),
            width=76, height=64, bgcolor=KEY_IDLE, border_radius=10,
            border=_border(1, BORDER), alignment=ft.Alignment.CENTER,
            on_click=None)
        # Mouse press-and-hold via gesture detector wrapper.
        gd = ft.GestureDetector(
            content=box,
            on_tap_down=lambda e, kk=k: self._mouse_down(kk),
            on_tap_up=lambda e, kk=k: self._mouse_up(kk),
            on_exit=lambda e, kk=k: self._mouse_up(kk))
        self.key_boxes[k] = box
        return gd

    def _build(self):
        p = self.page
        p.title = "G1 Keyboard Walk"
        p.bgcolor = BG
        p.theme_mode = ft.ThemeMode.DARK
        p.padding = 0
        p.window.width = 400
        p.window.height = 540
        p.window.resizable = True
        p.window.always_on_top = True
        p.on_keyboard_event = self._on_key

        title = ft.Text("G1 KEYBOARD WALK", size=17, weight=ft.FontWeight.W_900, color=ACCENT)

        self.enable_btn = ft.Switch(value=False, active_color=OK, on_change=self._on_enable)
        enable_row = ft.Row(
            [ft.Text("ENABLE DRIVE", size=13, weight=ft.FontWeight.W_900, color=FG),
             self.enable_btn],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN)

        # SPEED slider — scales the per-tap step (small nudge -> big lunge).
        self.speed_val = ft.Text(f"{self.speed:.2f} m/s", size=13,
                                 weight=ft.FontWeight.W_900, color=TURQUOISE)
        speed_hdr = ft.Row(
            [ft.Text("STEP SPEED", size=13, weight=ft.FontWeight.W_900, color=FG),
             self.speed_val],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
        self.speed_slider = ft.Slider(
            min=SPEED_MIN, max=SPEED_MAX, value=self.speed,
            active_color=TURQUOISE, on_change=self._on_speed)
        speed_col = ft.Column([speed_hdr, self.speed_slider], spacing=0)

        # WASDQE pad:  Q W E  /  A S D
        pad = ft.Column([
            ft.Row([self._key_box("q"), self._key_box("w"), self._key_box("e")],
                   spacing=8, alignment=ft.MainAxisAlignment.CENTER),
            ft.Row([self._key_box("a"), self._key_box("s"), self._key_box("d")],
                   spacing=8, alignment=ft.MainAxisAlignment.CENTER),
        ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

        self.stop_box = ft.Container(
            content=ft.Text("SPACE — STOP", size=13, weight=ft.FontWeight.W_900, color="#000000"),
            height=38, bgcolor=DANGER, border_radius=10, alignment=ft.Alignment.CENTER)

        self.vel_lbl = ft.Text("vx +0.00   vy +0.00   ω +0.00",
                               size=14, weight=ft.FontWeight.W_900, color=FG_VAL)
        self.status = ft.Text("", size=12, color=FG_DIM, weight=ft.FontWeight.W_700)

        hint = ft.Text("tap W A S D Q E — each tap lunges then STOPS · SPEED slider sets step size\n"
                       "SPACE = stop · SHIFT = boost · Z = damp+quit · ESC = stop+quit",
                       size=10, color=FG_DIM, weight=ft.FontWeight.W_700)

        card = ft.Container(
            content=ft.Column([
                title,
                enable_row,
                speed_col,
                ft.Divider(height=1, color=BORDER),
                pad,
                self.stop_box,
                self.vel_lbl,
                hint,
                self.status,
            ], spacing=12, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
            bgcolor=CARD2, border_radius=14, padding=16, border=_border(1, BORDER),
            margin=12)
        p.add(card)
        self._refresh_status()

    # ---- events -------------------------------------------------------- #
    def _on_speed(self, e):
        self.speed = float(e.control.value)
        self.speed_val.value = f"{self.speed:.2f} m/s"
        self._safe_update()

    def _on_enable(self, e):
        self.enabled = bool(e.control.value)
        if self.enabled:
            # Engage the walking gait (FSM 200) so Move() actually walks.
            code = self.back.start_walk()
            if code not in (0, None):
                self._lease_hint(code)
        else:
            self.vx = self.vy = self.omega = 0.0
            self.back.stop()
        self._refresh_status()

    def _lease_hint(self, code):
        """Explain a failed loco RPC. 3102 = RPC_ERR_CLIENT_SEND: the request
        can't reach the loco service. Root cause found on this robot: the SDK
        service name ('loco') didn't match the firmware's ('sport', ai_sport
        >= 8.2.0.0). If you still see 3102, the --loco-service name is wrong for
        this firmware."""
        if code == 3102:
            self.status.value = ("⚠ 3102: can't reach the loco service — SDK/firmware "
                                 f"service-name mismatch. Currently using "
                                 f"'{self.back.loco_service}'. Try the other one via "
                                 "--loco-service (sport <-> loco).")
        else:
            self.status.value = (f"⚠ loco RPC failed (code {code}). Stand the robot into "
                                 f"operation mode (remote joystick should walk it), then ENABLE.")
        self.status.color = ERR
        self._safe_update()

    def _tap_pulse(self, k: str):
        """One tap = one bounded velocity pulse along the key's axis, held for
        PULSE_TICKS then auto-stopped. Re-tapping just re-fires the same fixed
        pulse — it never accumulates. Magnitude comes from the SPEED slider
        (turning scaled by ANG_RATIO); SHIFT adds a temporary boost."""
        axis, sign, _, _ = DRIVE_KEYS[k]
        mag = self.speed * ANG_RATIO if axis == "omega" else self.speed
        if self._shift:
            mag *= PULSE_FAST
        self._pulse_vel[axis] = sign * mag
        self._pulse_ticks[axis] = PULSE_TICKS

    def _reset_pulses(self):
        for a in self._pulse_ticks:
            self._pulse_ticks[a] = 0
            self._pulse_vel[a] = 0.0

    def _mouse_down(self, k: str):
        if k not in DRIVE_KEYS:
            return
        self._last_key[k] = time.monotonic()   # feedback highlight
        self._mouse_held.add(k)
        if self.enabled:
            self._tap_pulse(k)

    def _mouse_up(self, k: str):
        self._mouse_held.discard(k)

    def _on_key(self, e: ft.KeyboardEvent):
        self._shift = bool(e.shift)
        key = (e.key or "").lower()
        if key in ("escape", "esc"):
            self.back.stop(); self.back.zero_torque(); self.page.window.close(); return
        if key in ("z",):
            self.back.damp(); self.page.window.close(); return
        if key in (" ", "space"):
            self.vx = self.vy = self.omega = 0.0
            self.back.stop()
            return
        if key in DRIVE_KEYS:
            self._last_key[key] = time.monotonic()
            if self.enabled:
                self._tap_pulse(key)

    # ---- drive loop ---------------------------------------------------- #
    async def _loop(self):
        import asyncio
        while True:
            if self.enabled:
                # Run out any active tap pulses, then STOP that axis.
                self._advance_pulses()
                code = self.back.move(self.vx, self.vy, self.omega)
                # Flag persistent lease/authority rejections (e.g. 3102) live.
                if code not in (0, None):
                    self._lease_hint(code)
            else:
                self.vx = self.vy = self.omega = 0.0
                self._reset_pulses()

            self._paint()
            await asyncio.sleep(TICK_DT)

    def _advance_pulses(self):
        """Apply each axis's active pulse velocity until its ticks run out, then
        STOP that axis. Pulses don't accumulate — each tap sets a fixed velocity
        for a fixed number of ticks. Clamped per axis to the hard safety ceiling."""
        for axis, vmax in (("vx", VMAX_LIN), ("vy", VMAX_LIN), ("omega", VMAX_ANG)):
            if self._pulse_ticks[axis] > 0:
                self._pulse_ticks[axis] -= 1
                setattr(self, axis, _clamp(self._pulse_vel[axis], vmax))
                if self._pulse_ticks[axis] == 0:
                    self._pulse_vel[axis] = 0.0
            else:
                setattr(self, axis, 0.0)

    # ---- render -------------------------------------------------------- #
    def _paint(self):
        for k, box in self.key_boxes.items():
            # Highlight on any press (keyboard OR mouse), regardless of ENABLE —
            # this is pure press feedback so it's obvious the input registered.
            hot = self._active(k)
            box.bgcolor = KEY_HOT if hot else KEY_IDLE
            box.border = _border(1, TURQUOISE if hot else BORDER)
            box.content.controls[0].color = "#000000" if hot else FG
            box.content.controls[1].color = "#000000" if hot else FG_DIM
        self.vel_lbl.value = f"vx {self.vx:+.2f}   vy {self.vy:+.2f}   ω {self.omega:+.2f}"
        self._safe_update()

    def _refresh_status(self):
        if self.back.dry_run:
            self.status.value = "○ DRY-RUN (no robot)"; self.status.color = FG_DIM
        elif self.back.ready:
            self.status.value = (f"● READY on {self.iface}"
                                 + ("  —  DRIVE ENABLED" if self.enabled else "  —  drive OFF"))
            self.status.color = OK if self.enabled else TEAL
        else:
            self.status.value = f"○ {self.back.error or 'no LocoClient'}"; self.status.color = ERR
        self._safe_update()

    def _safe_update(self):
        try:
            self.page.update()
        except Exception:
            pass


def main(page: ft.Page):
    ap = argparse.ArgumentParser(description="Walk the G1 with the keyboard (Flet)")
    ap.add_argument("--iface", default=None,
                    help="DDS interface (default: auto-detect the 192.168.123.x NIC)")
    ap.add_argument("--dry-run", action="store_true", help="UI only, no robot / DDS")
    ap.add_argument("--loco-service", default="sport", choices=["sport", "loco"],
                    help="loco RPC service name: 'sport' for ai_sport >= 8.2.0.0 "
                         "firmware (default), 'loco' for older firmware")
    ap.add_argument("--verbose-sdk", action="store_true",
                    help="don't filter the harmless [LeaseClient]/[ClientStub] "
                         "3102 chatter from the vendored SDK")
    args, _ = ap.parse_known_args()
    if not args.dry_run and not args.verbose_sdk:
        _install_noise_filter()
    iface = args.iface if args.iface else _resolve_g1_iface()
    if not args.dry_run:
        print(f"[keyboard_walk] using DDS interface: {iface or '(none found on 192.168.123.x)'}")
        print(f"[keyboard_walk] loco service name: {args.loco_service}")
    KeyboardWalkApp(page, iface=iface or "", dry_run=args.dry_run,
                    loco_service=args.loco_service)


if __name__ == "__main__":
    ft.run(main)
