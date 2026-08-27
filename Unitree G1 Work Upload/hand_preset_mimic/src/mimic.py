#!/usr/bin/env python3
"""Preset-based hand mimicry — camera -> MediaPipe gesture -> preset animation -> Inspire hand.

Detects 3 gestures and plays back recorded animations (or static angles):
  - 2-finger pinch (thumb+index)        -> presets/2finger_pinch/
  - 3-finger pinch (thumb+index+middle) -> presets/3finger_pinch/
  - Grab (full fist)                    -> presets/grab/
  - (none)                              -> open position (all zeros)

Preset lookup order per gesture folder:
  1. animation.yaml  (recorded path from recorder.py) — plays frame-by-frame then holds last
  2. preset.yaml     (static endpoint fallback)

Usage:
    python src/mimic.py --source 8 --config config/hand_preset_mimic.yaml
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from _source import FrameSource

from dexkit import get_logger
from dexkit.perception import HandTracker, draw_landmarks

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
LOG_DIR       = PROJECT_ROOT / "logs"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"
PRESETS_DIR   = PROJECT_ROOT / "presets"

GESTURE_2PINCH = "2finger_pinch"
GESTURE_3PINCH = "3finger_pinch"
GESTURE_GRAB   = "grab"
GESTURE_NONE   = "none"   # no hand visible

OPEN_ANGLES = [0, 0, 0, 0, 0, 0]

GESTURE_COLORS = {
    GESTURE_GRAB:   (0,  100, 255),   # orange
    GESTURE_3PINCH: (255, 100,   0),  # blue
    GESTURE_2PINCH: (0,    0, 255),   # red
}
GESTURE_TIPS = {
    GESTURE_GRAB:   [4, 8, 12, 16, 20],
    GESTURE_3PINCH: [4, 8, 12],
    GESTURE_2PINCH: [4, 8],
}


# ---------------------------------------------------------------------------
# Preset loading
# ---------------------------------------------------------------------------

class Preset:
    """Holds either a static endpoint or a full animation sequence per side."""

    def __init__(self, gesture: str):
        self.gesture  = gesture
        self.fps: float = 25.0
        # frames: list of {side: [6 ints]}, or None for static
        self._frames: list[dict[str, list[int]]] | None = None
        self._static: list[int] | None = None
        self._load()

    def _load(self):
        anim_path   = PRESETS_DIR / self.gesture / "animation.yaml"
        static_path = PRESETS_DIR / self.gesture / "preset.yaml"

        if anim_path.exists():
            data = yaml.safe_load(anim_path.read_text()) or {}
            self.fps = float(data.get("fps", 25.0))
            raw = data.get("frames", {})
            sides = list(raw.keys())
            if sides:
                n = max(len(raw[s]) for s in sides)
                self._frames = []
                for i in range(n):
                    self._frames.append({
                        s: raw[s][min(i, len(raw[s]) - 1)] for s in sides
                    })
        elif static_path.exists():
            data = yaml.safe_load(static_path.read_text()) or {}
            # Support per-side angles (angles_left / angles_right) or shared
            left  = data.get("angles_left")  or data.get("angles")
            right = data.get("angles_right") or data.get("angles")
            if left and len(left) == 6 and right and len(right) == 6:
                self._static_per_side = {"left": [int(a) for a in left],
                                          "right": [int(a) for a in right]}
            elif left and len(left) == 6:
                self._static = [int(a) for a in left]

    @property
    def is_animation(self) -> bool:
        return self._frames is not None

    @property
    def num_frames(self) -> int:
        return len(self._frames) if self._frames else 0

    def frame(self, idx: int) -> dict[str, list[int]]:
        """Return {side: angles} for frame idx (clamped to last frame)."""
        if not self._frames:
            return {}
        return self._frames[min(idx, len(self._frames) - 1)]

    def static_angles(self, side: str = "left") -> list[int]:
        per = getattr(self, "_static_per_side", None)
        if per:
            return per.get(side, list(per.values())[0])
        return getattr(self, "_static", None) or OPEN_ANGLES


# ---------------------------------------------------------------------------
# Animation player — runs in background thread, sends frames to hand
# ---------------------------------------------------------------------------

class AnimPlayer:
    """Background thread that streams animation frames to one hand."""

    def __init__(self, hand, side: str):
        self._hand   = hand
        self._side   = side
        self._lock   = threading.Lock()
        self._preset: Preset | None = None
        self._running = True
        self._cond   = threading.Condition(self._lock)
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def play(self, preset: Preset):
        with self._cond:
            self._preset = preset
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._running = False
            self._cond.notify()

    def _loop(self):
        while True:
            with self._cond:
                while self._running and self._preset is None:
                    self._cond.wait()
                if not self._running:
                    return
                preset = self._preset
                self._preset = None

            if preset is None:
                continue

            if not preset.is_animation:
                angles = preset.static_angles(self._side)
                if self._hand:
                    self._hand.set_angles(angles, wait=False)
                continue

            dt = 1.0 / preset.fps
            for fi in range(preset.num_frames):
                with self._cond:
                    # Preempt if a new gesture arrived
                    if self._preset is not None or not self._running:
                        break
                frame  = preset.frame(fi)
                angles = frame.get(self._side, preset.static_angles(self._side))
                if self._hand:
                    self._hand.set_angles(angles, wait=False)
                time.sleep(dt)
            # Hold last frame — player waits for next play() call


# ---------------------------------------------------------------------------
# Gesture detection (identical to hand_mimic)
# ---------------------------------------------------------------------------

def _detect_gesture(lm, pinch_threshold: float) -> str:
    lma    = np.asarray(lm, dtype=np.float32)
    palm   = float(np.linalg.norm(lma[5] - lma[17])) + 1e-6
    palm_c = (lma[5] + lma[17]) / 2.0

    if all(float(np.linalg.norm(lma[tip] - palm_c)) / palm < 0.75
           for tip in (8, 12, 16, 20)):
        return GESTURE_GRAB

    d_idx = float(np.linalg.norm(lma[4] - lma[8]))  / palm
    d_mid = float(np.linalg.norm(lma[4] - lma[12])) / palm
    if d_idx < pinch_threshold and d_mid < pinch_threshold:
        return GESTURE_3PINCH
    if d_idx < pinch_threshold:
        return GESTURE_2PINCH
    return GESTURE_OPEN


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="hand_preset_mimic")
    ap.add_argument("--source",          default="8")
    ap.add_argument("--model",           type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--width",           type=int,  default=1920)
    ap.add_argument("--height",          type=int,  default=1080)
    ap.add_argument("--left-port",       default="/dev/ttyUSB0")
    ap.add_argument("--right-port",      default="/dev/ttyUSB1")
    ap.add_argument("--left-id",         type=int,  default=1)
    ap.add_argument("--right-id",        type=int,  default=1)
    ap.add_argument("--baud",            type=int,  default=115200)
    ap.add_argument("--hand-speed",      type=int,  default=500)
    ap.add_argument("--mirror",          action="store_true")
    ap.add_argument("--swap-hands",      action="store_true")
    ap.add_argument("--gpu",             action="store_true")
    ap.add_argument("--config",          type=Path, default=None)
    ap.add_argument("--dry-run",         action="store_true")
    ap.add_argument("--pinch-threshold", type=float, default=0.5262)
    ap.add_argument("--no-window",       action="store_true")
    args = ap.parse_args()

    log = get_logger("mimic", log_dir=LOG_DIR)

    if not args.model.exists():
        log.error("model not found: %s", args.model); return 1

    pinch_threshold = args.pinch_threshold
    if args.config and Path(args.config).exists():
        raw = yaml.safe_load(Path(args.config).read_text()) or {}
        pinch_threshold = float(
            (raw.get("calibration") or {}).get("pinch_threshold", pinch_threshold))

    try:
        source = FrameSource(args.source, width=args.width, height=args.height)
    except Exception:
        log.exception("cannot open source %s", args.source); return 1
    if not source.opened():
        log.error("cannot open source %s", source.describe()); return 1
    log.info("source opened %s", source.describe())

    try:
        tracker = HandTracker(str(args.model), num_hands=2,
                              delegate="gpu" if args.gpu else "cpu")
        log.info("tracker loaded delegate=%s", "gpu" if args.gpu else "cpu")
    except Exception:
        log.exception("failed to load tracker"); source.release(); return 1

    # Load all presets (animation.yaml wins over preset.yaml)
    presets: dict[str, Preset] = {}
    for g in (GESTURE_2PINCH, GESTURE_3PINCH, GESTURE_GRAB):
        p = Preset(g)
        presets[g] = p
        if p.is_animation:
            log.info("preset %s: animation %d frames @ %.0f fps", g, p.num_frames, p.fps)
        else:
            log.info("preset %s: static L=%s R=%s", g,
                     p.static_angles("left"), p.static_angles("right"))

    # "no hand visible" → open position (all zeros)
    open_preset = Preset.__new__(Preset)
    open_preset.gesture = "open"
    open_preset.fps     = 25.0
    open_preset._frames = None
    open_preset._static = OPEN_ANGLES
    no_hand_preset = open_preset

    channels: dict[str, dict] = {}
    if not args.dry_run:
        from dexkit.hands import InspireHand
        for side, (port, hid) in {"left":  (args.left_port,  args.left_id),
                                   "right": (args.right_port, args.right_id)}.items():
            if port.lower() == "none":
                continue
            try:
                hand = InspireHand(port=port, baud=args.baud, hand_id=hid)
                hand.set_speed([args.hand_speed] * 6)
                hand.set_force([400] * 6)
                player = AnimPlayer(hand, side)
                channels[side] = {"hand": hand, "player": player, "last_gesture": None}
                log.info("connected %s port=%s", side, port)
            except Exception:
                log.exception("failed to open %s on %s", side, port)

    if not channels:
        for side in ("left", "right"):
            channels[side] = {"hand": None, "player": None, "last_gesture": None}
        log.info("no hands — perception only")

    win = "hand_preset_mimic"
    if not args.no_window:
        print("q=quit  red=2-pinch  blue=3-pinch  orange=grab")
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 1280, 720)
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed"); break
            if args.mirror:
                frame = cv2.flip(frame, 1)

            result = tracker.process(frame)
            draw_landmarks(frame, result)

            seen: set[str] = set()
            fh, fw = frame.shape[:2]

            for i in range(result.num_hands):
                label    = result.handedness[i] if i < len(result.handedness) else "?"
                side_raw = label.lower()
                if side_raw not in ("left", "right"):
                    continue
                side = ("right" if side_raw == "left" else "left") if args.swap_hands else side_raw
                ch   = channels.get(side)
                if ch is None:
                    continue
                seen.add(side)

                lm      = result.landmarks[i]
                gesture = _detect_gesture(lm, pinch_threshold)

                color = GESTURE_COLORS.get(gesture)
                if color:
                    for tip in GESTURE_TIPS[gesture]:
                        tx, ty = int(lm[tip][0] * fw), int(lm[tip][1] * fh)
                        cv2.circle(frame, (tx, ty), 9, color, -1)

                disp = "Right" if label == "Left" else "Left"
                tag  = {"2finger_pinch": "[2-PINCH]", "3finger_pinch": "[3-PINCH]",
                        "grab": "[GRAB]"}.get(gesture, "")
                cv2.putText(frame, f"{disp}{tag}",
                            (10, 30 + 26 * len(seen)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 1)

                if gesture != ch["last_gesture"]:
                    ch["last_gesture"] = gesture
                    preset = presets.get(gesture, no_hand_preset)
                    if ch["player"] is not None:
                        ch["player"].play(preset)
                        log.info("%s -> %s (anim=%s)", side, gesture, preset.is_animation)
                    else:
                        angles = preset.static_angles(side)
                        print(f"[dry-run] {side}: {gesture} -> {angles}")

            for side, ch in channels.items():
                if side not in seen and ch["last_gesture"] is not None:
                    ch["last_gesture"] = None
                    if ch["player"] is not None:
                        ch["player"].play(no_hand_preset)
                    else:
                        print(f"[dry-run] {side}: -> open")

            if not args.no_window:
                cv2.putText(frame, "q=quit  red=2pinch  blue=3pinch  orange=grab",
                            (10, fh - 15), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (200, 200, 200), 1)
                cv2.imshow(win, frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except Exception:
        log.exception("mimic loop crashed"); return 1
    finally:
        for ch in channels.values():
            if ch["player"] is not None:
                ch["player"].stop()
            if ch["hand"] is not None:
                ch["hand"].set_angles(OPEN_ANGLES, wait=False)
                ch["hand"].close()
        tracker.close()
        source.release()
        cv2.destroyAllWindows()
        log.info("closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
