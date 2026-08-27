#!/usr/bin/env python3
"""Hybrid mimic v2 — calibrated geometric (4 fingers) + dex pinch (index + thumb).

Geom base from geom-v3 (calibrated, z-flip, c=calibrate, t=reload).
Dex pinch from hybrid-v1 (DexPilot overrides index + thumb_bend + thumb_rot
only when pinch detected). Right hand → right Inspire.

    uv run python src/mimic_hybrid.py --source 9 --mirror --gpu
    uv run python src/mimic_hybrid.py --right-port none   # no hardware
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import numpy as np
import yaml

from _source import FrameSource
from _config import load_calibration
from calibrate_hand import run_calibration
from dexkit import get_logger
from dexkit.mapping import AngleSmoother, landmarks_to_angles
from dexkit.mapping.retarget import _palm_size
from dexkit.mapping.dex_backend import DexRetarget
from dexkit.perception import HandTracker, draw_landmarks

PROJECT_ROOT   = Path(__file__).resolve().parent.parent
LOG_DIR        = PROJECT_ROOT / "logs"
DEFAULT_MODEL  = PROJECT_ROOT / "config" / "hand_landmarker.task"
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "hand_mimic.yaml"


def _screen_size() -> tuple[int, int]:
    """Best-effort primary-monitor resolution (w, h): xrandr, then tkinter,
    then 1920x1080. Used to force-fill the window since GNOME/mutter doesn't
    reliably honour OpenCV's WND_PROP_FULLSCREEN."""
    import subprocess
    try:
        out = subprocess.run(["xrandr"], capture_output=True, text=True,
                             timeout=2).stdout
        for line in out.splitlines():
            if " connected" in line:
                for tok in line.split():
                    if "x" in tok and tok[0].isdigit():
                        w, h = tok.split("+")[0].split("x")
                        return int(w), int(h)
    except Exception:
        pass
    try:
        import tkinter
        r = tkinter.Tk(); r.withdraw()
        wh = (r.winfo_screenwidth(), r.winfo_screenheight())
        r.destroy()
        return wh
    except Exception:
        return (1920, 1080)


def _apply_fullscreen(win: str, on: bool) -> None:
    """Fill the screen (or restore). Sets WND_PROP_FULLSCREEN *and* explicitly
    resizes/moves the window, because the property alone is ignored by
    GNOME/mutter. Does NOT touch the frame — the camera stays at native res and
    OpenCV scales it into the window (no blurry pre-upscale)."""
    if on:
        sw, sh = _screen_size()
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
        cv2.resizeWindow(win, sw, sh)
        cv2.moveWindow(win, 0, 0)
    else:
        cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, 1280, 960)


def _pinch_dist(lm) -> float:
    lma  = np.asarray(lm, dtype=np.float32)
    palm = _palm_size(lma)
    return float(np.linalg.norm(lma[4] - lma[8])) / (palm + 1e-6)


def _physical_side(label: str, swap: bool) -> str:
    s = label.lower()
    if s not in ("left", "right"):
        return s
    return ("right" if s == "left" else "left") if swap else s


def _deadband(angles, last, db):
    if last is None:
        return list(angles), True
    out, changed = list(last), False
    for i in range(6):
        if abs(angles[i] - last[i]) >= db:
            out[i] = angles[i]; changed = True
    return out, changed


def _load_pinch_threshold(config_path) -> float:
    try:
        raw = yaml.safe_load(Path(config_path).read_text()) if config_path else {}
        return float((raw.get("calibration") or {}).get("pinch_threshold", 0.5262))
    except Exception:
        return 0.5262


def main() -> int:
    ap = argparse.ArgumentParser(description="Hybrid mimic v2 — geom + dex pinch")
    ap.add_argument("--source",           default="9")
    ap.add_argument("--model",            type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--width",            type=int,  default=1920)
    ap.add_argument("--height",           type=int,  default=1080)
    ap.add_argument("--left-port",        default="/dev/ttyUSB0")
    ap.add_argument("--right-port",       default="/dev/ttyUSB1")
    ap.add_argument("--baud",             type=int,  default=115200)
    ap.add_argument("--hand-speed",       type=int,  default=1000)
    ap.add_argument("--fps",              type=float, default=60.0)
    ap.add_argument("--alpha",            type=float, default=0.6)
    ap.add_argument("--thumb-alpha",      type=float, default=0.25)
    ap.add_argument("--deadband",         type=int,  default=8)
    ap.add_argument("--pinch-rot-offset", type=int,  default=10)
    ap.add_argument("--dex-alpha",        type=float, default=1.0)
    ap.add_argument("--no-swap",          dest="swap", action="store_false")
    ap.set_defaults(swap=False)
    ap.add_argument("--gpu",              action="store_true")
    ap.add_argument("--mirror",           action="store_true")
    ap.add_argument("--windowed",         dest="fullscreen", action="store_false")
    ap.set_defaults(fullscreen=True)
    ap.add_argument("--config",           type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run",          action="store_true")
    args = ap.parse_args()

    log = get_logger("mimic_hybrid", log_dir=LOG_DIR)
    log.info("hybrid v2 mode — geom base + dex pinch")

    try:
        source = FrameSource(args.source, width=args.width, height=args.height)
    except Exception:
        log.exception("cannot open source %s", args.source); return 1
    if not source.opened():
        log.error("source not opened"); return 1
    log.info("source opened %s", source.describe())

    calib = load_calibration(args.config, log)
    pinch_threshold = _load_pinch_threshold(args.config)

    try:
        tracker = HandTracker(str(args.model), num_hands=2,
                              delegate="gpu" if args.gpu else "cpu")
    except Exception:
        log.exception("tracker failed"); source.release(); return 1

    try:
        dex_ret = {
            "left":  DexRetarget("left",  low_pass_alpha=args.dex_alpha, use_dexpilot=True),
            "right": DexRetarget("right", low_pass_alpha=args.dex_alpha, use_dexpilot=True),
        }
        log.info("dex-retargeting loaded (DexPilot)")
    except Exception:
        log.exception("failed to load dex-retargeting"); source.release(); tracker.close(); return 1

    def make_smoother():
        if getattr(calib, "pinch_alpha", None) and len(calib.pinch_alpha) == 6:
            return AngleSmoother(alpha=list(calib.pinch_alpha))
        return AngleSmoother(alpha=[args.alpha] * 4 + [args.thumb_alpha] * 2)

    channels: dict[str, dict] = {}
    if not args.dry_run:
        from dexkit.hands import InspireHand
        for side, port in {"left": args.left_port, "right": args.right_port}.items():
            if port.lower() == "none":
                continue
            try:
                h = InspireHand(port=port, baud=args.baud, hand_id=1)
                h.set_speed([args.hand_speed] * 6)
                h.set_force([400] * 6)
                channels[side] = {"hand": h, "smoother": make_smoother(),
                                   "last_sent": None, "last_cmd": 0.0}
                log.info("connected %s %s", side, port)
            except Exception:
                log.exception("failed %s %s", side, port)
    if not channels:
        for side in ("left", "right"):
            channels[side] = {"hand": None, "smoother": make_smoother(),
                               "last_sent": None, "last_cmd": 0.0}

    win = "mimic_hybrid"
    min_dt = 1.0 / args.fps if args.fps > 0 else 0.0
    flip_z = False
    pinch_samples: list[float] = []
    PINCH_SAMPLES_NEEDED = 5

    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    win_shown = False   # defer fullscreen until after first imshow (WM reliability)
    print("q=quit  f=fullscreen  z=palm/backhand  c=calibrate  t=reload  P=pinch sample x5")

    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed"); break
            if args.mirror:
                frame = cv2.flip(frame, 1)

            result = tracker.process(frame)
            draw_landmarks(frame, result)

            mode = "PALM" if flip_z else "BACKHAND"
            cv2.putText(frame, f"HYBRID v2 | {mode}",
                        (10, frame.shape[0] - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

            seen: set[str] = set()
            now = time.monotonic()

            for i in range(result.num_hands):
                label = result.handedness[i] if i < len(result.handedness) else "?"
                side     = _physical_side(label, args.swap)
                rot_side = _physical_side(label, True)
                ch = channels.get(side)
                if ch is None:
                    continue
                seen.add(side)

                lm = result.landmarks[i]

                # Pinch detection
                dist     = _pinch_dist(lm)
                is_pinch = dist < pinch_threshold

                # Geom base
                try:
                    geom_lm = lm
                    if flip_z:
                        geom_lm = np.array(lm, dtype=np.float32)
                        geom_lm[:, 2] = -geom_lm[:, 2]
                    raw, _ = landmarks_to_angles(geom_lm, calib, hand_side=rot_side)
                except Exception:
                    log.exception("retarget failed (%s)", side); continue

                # Dex pinch override — index + thumb only
                if is_pinch:
                    try:
                        dr = dex_ret.get(label.lower())
                        if dr is not None:
                            wl = (result.world_landmarks[i]
                                  if result.world_landmarks and i < len(result.world_landmarks)
                                  else lm)
                            da = list(dr.retarget(wl))
                            raw = list(raw)
                            raw[3] = da[3]
                            raw[4] = da[4]
                            raw[5] = max(0, min(1000, da[5] + args.pinch_rot_offset))
                    except Exception:
                        log.exception("dex pinch override failed (%s)", side)

                angles = ch["smoother"].update(raw)

                # Red dots on thumb + index tip during pinch
                if is_pinch:
                    fh, fw = frame.shape[:2]
                    for tip in (4, 8):
                        tx, ty = int(lm[tip][0] * fw), int(lm[tip][1] * fh)
                        cv2.circle(frame, (tx, ty), 9, (0, 0, 255), -1)

                tag = " [PINCH]" if is_pinch else ""
                cv2.putText(frame, f"{side}{tag}: {angles}",
                            (10, 30 + 26 * len(seen)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                if now - ch["last_cmd"] >= min_dt:
                    out, changed = _deadband(angles, ch["last_sent"], args.deadband)
                    if changed:
                        ch["last_cmd"] = now; ch["last_sent"] = out
                        if ch["hand"] is not None:
                            ch["hand"].set_angles(out, wait=False)
                        else:
                            print(f"[dry-run] {side}: {out}", end="\r")

            for side, ch in channels.items():
                if side not in seen:
                    ch["smoother"].reset(); ch["last_sent"] = None

            if pinch_samples:
                cv2.putText(frame, f"[P] pinch samples: {len(pinch_samples)}/{PINCH_SAMPLES_NEEDED}",
                            (10, frame.shape[0] - 40),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 165, 255), 1)

            cv2.imshow(win, frame)
            if not win_shown:
                # Apply fullscreen now that the window has real content — this
                # is what actually makes it span the whole screen under GNOME.
                win_shown = True
                if args.fullscreen:
                    _apply_fullscreen(win, True)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("f"):
                args.fullscreen = not args.fullscreen
                _apply_fullscreen(win, args.fullscreen)
            if key == ord("z"):
                flip_z = not flip_z
                log.info("palm mode: %s", flip_z)
            if key == ord("c") or key == ord("C"):
                log.info("entering calibration")
                run_calibration(source, tracker, win,
                                mirror=args.mirror, swap_hands=args.swap, log=log,
                                calib=calib)
                calib = load_calibration(args.config, log)
                pinch_threshold = _load_pinch_threshold(args.config)
                for ch_ in channels.values():
                    ch_["smoother"] = make_smoother(); ch_["last_sent"] = None
            if key == ord("t") or key == ord("T"):
                calib = load_calibration(args.config, log)
                pinch_threshold = _load_pinch_threshold(args.config)
                for ch_ in channels.values():
                    ch_["smoother"] = make_smoother(); ch_["last_sent"] = None
                log.info("config reloaded — pinch_threshold=%.4f", pinch_threshold)
            if key == ord("p") or key == ord("P"):
                sample_dist = None
                for ri in range(result.num_hands):
                    rl = result.handedness[ri] if ri < len(result.handedness) else ""
                    if _physical_side(rl, args.swap) == "right":
                        sample_dist = _pinch_dist(result.landmarks[ri]); break
                if sample_dist is None and result.num_hands:
                    sample_dist = _pinch_dist(result.landmarks[0])
                if sample_dist is not None:
                    pinch_samples.append(sample_dist)
                    n = len(pinch_samples)
                    print(f"  sample {n}/{PINCH_SAMPLES_NEEDED}: dist={sample_dist:.4f}")
                    if n >= PINCH_SAMPLES_NEEDED:
                        pinch_threshold = float(np.mean(pinch_samples)) * 1.2
                        pinch_samples.clear()
                        cfg = yaml.safe_load(Path(args.config).read_text()) if Path(args.config).exists() else {}
                        cfg.setdefault("calibration", {})["pinch_threshold"] = round(pinch_threshold, 4)
                        Path(args.config).write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
                        print(f"  pinch threshold set to {pinch_threshold:.4f}")
                        log.info("pinch threshold updated: %.4f", pinch_threshold)

    except Exception:
        log.exception("loop crashed"); return 1
    finally:
        for ch in channels.values():
            if ch["hand"]: ch["hand"].close()
        tracker.close(); source.release(); cv2.destroyAllWindows()
        log.info("mimic_hybrid closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
