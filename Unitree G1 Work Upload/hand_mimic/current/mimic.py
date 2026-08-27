#!/usr/bin/env python3
"""End-to-end hand mimicry — camera -> MediaPipe -> retarget -> Inspire hand(s).

    python src/mimic.py --source 9 --width 1920 --height 1080 --mirror --gpu --config config/hand_mimic.yaml

Logs to logs/mimic.log.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2
import yaml

from _config import load_calibration
from _source import FrameSource
from calibrate_hand import run_calibration

from dexkit import get_logger
from dexkit.mapping import AngleSmoother, landmarks_to_angles
from dexkit.perception import HandTracker, draw_landmarks

import numpy as np

PROJECT_ROOT  = Path(__file__).resolve().parent.parent
LOG_DIR       = PROJECT_ROOT / "logs"
DEFAULT_MODEL = PROJECT_ROOT / "config" / "hand_landmarker.task"


def _pinch_dist(lm) -> float:
    """Normalised thumb-tip to index-tip distance."""
    from dexkit.mapping.retarget import _palm_size
    lma  = np.asarray(lm, dtype=np.float32)
    palm = _palm_size(lma)
    return float(np.linalg.norm(lma[4] - lma[8])) / (palm + 1e-6)


def _hand_state(lm, threshold: float):
    """Return 'fist', '3pinch', '2pinch', or None.

    Fist:    ALL 4 finger tips (index+middle+ring+pinky) close to palm centre.
             This means the whole hand is curled — a peace sign can never be a fist
             because index+middle tips are far from palm centre when extended.
    3-pinch: thumb close to both index+middle, but NOT a fist.
    2-pinch: thumb close to index only.
    Priority: fist > 3-pinch > 2-pinch.
    """
    from dexkit.mapping.retarget import _palm_size
    lma  = np.asarray(lm, dtype=np.float32)
    palm = _palm_size(lma)
    palm_c = (lma[5] + lma[17]) / 2.0

    # All fingertip distances from palm centre
    d_idx_palm   = float(np.linalg.norm(lma[8]  - palm_c)) / (palm + 1e-6)
    d_mid_palm   = float(np.linalg.norm(lma[12] - palm_c)) / (palm + 1e-6)
    d_ring_palm  = float(np.linalg.norm(lma[16] - palm_c)) / (palm + 1e-6)
    d_pinky_palm = float(np.linalg.norm(lma[20] - palm_c)) / (palm + 1e-6)

    # Fist: ALL 4 fingertips curled toward palm (threshold 0.75 palm-widths)
    all_curled = (d_idx_palm < 0.75 and d_mid_palm < 0.75 and
                  d_ring_palm < 0.75 and d_pinky_palm < 0.75)
    if all_curled:
        return "fist"

    # Pinch: thumb tip close to finger tips
    d_idx = float(np.linalg.norm(lma[4] - lma[8]))  / (palm + 1e-6)
    d_mid = float(np.linalg.norm(lma[4] - lma[12])) / (palm + 1e-6)

    if d_idx < threshold and d_mid < threshold:
        return "3pinch"
    if d_idx < threshold:
        return "2pinch"
    return None


def _detect_pinch(lm, threshold: float) -> bool:
    return _pinch_dist(lm) < threshold


def _reload(config_path, log) -> tuple:
    """Reload calibration + pinch_threshold from config. Returns (calib, threshold)."""
    calib = load_calibration(config_path, log)
    try:
        raw = yaml.safe_load(Path(config_path).read_text()) if config_path else {}
        threshold = float((raw.get("calibration") or {}).get("pinch_threshold", 0.5262))
    except Exception:
        threshold = 0.5262
    log.info("config reloaded — pinch_threshold=%.4f", threshold)
    return calib, threshold


def _physical_side(label: str, swap: bool) -> str:
    side = label.lower()
    if side not in ("left", "right"):
        return side
    return ("right" if side == "left" else "left") if swap else side


def _apply_deadband(angles, last_sent, deadband):
    if last_sent is None:
        return list(angles), True
    out, changed = list(last_sent), False
    for i in range(6):
        # Thumb channels (4=bend, 5=rot) get a wider deadband to suppress jitter
        db = deadband * 6 if i >= 4 else deadband
        if abs(angles[i] - last_sent[i]) >= db:
            out[i] = angles[i]
            changed = True
    return out, changed


def main() -> int:
    ap = argparse.ArgumentParser(description="hand_mimic end-to-end demo")
    ap.add_argument("--source",      default="0")
    ap.add_argument("--model",       type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--width",       type=int,  default=640)
    ap.add_argument("--height",      type=int,  default=480)
    ap.add_argument("--left-port",   default="/dev/ttyUSB0")
    ap.add_argument("--right-port",  default="/dev/ttyUSB1")
    ap.add_argument("--left-id",     type=int,  default=1)
    ap.add_argument("--right-id",    type=int,  default=1)
    ap.add_argument("--baud",        type=int,  default=115200)
    ap.add_argument("--hand-speed",  type=int,  default=2000)
    ap.add_argument("--fps",         type=float, default=60.0)
    ap.add_argument("--alpha",       type=float, default=1.0)
    ap.add_argument("--thumb-alpha", type=float, default=1.0)
    ap.add_argument("--deadband",    type=int,  default=4)
    ap.add_argument("--swap-hands",    dest="swap_hands", action="store_true")
    ap.add_argument("--no-swap-hands", dest="swap_hands", action="store_false")
    ap.set_defaults(swap_hands=False)
    ap.add_argument("--gpu",         action="store_true")
    ap.add_argument("--mirror",      action="store_true")
    ap.add_argument("--config",      type=Path, default=None)
    ap.add_argument("--windowed",    dest="fullscreen", action="store_false")
    ap.set_defaults(fullscreen=True)
    ap.add_argument("--window-size", default="1280x960")
    ap.add_argument("--no-window",   action="store_true")
    ap.add_argument("--max-frames",  type=int,  default=300)
    ap.add_argument("--dry-run",     action="store_true")
    ap.add_argument("--backend", choices=["geom", "dex", "hybrid"], default="geom",
                    help="geom = calibrated geometric mapping; "
                         "dex = full dex-retargeting; "
                         "hybrid = geom everywhere + dex index/thumb ONLY during a pinch")
    ap.add_argument("--dex-alpha", type=float, default=1.0,
                    help="dex internal low-pass (1.0=no filter/fastest, lower=smoother)")
    ap.add_argument("--pinch-rot-offset", type=int, default=10,
                    help="fine-tune thumb_rot during a pinch (+ = more spread, - = more tucked)")
    ap.add_argument("--dexpilot", action="store_true",
                    help="use DexPilot (pinch-coupled, lifts other fingers near pinch, "
                         "slower) instead of the default vector retargeting")
    ap.add_argument("--flip-thumb-bend", action="store_true",
                    help="invert dex thumb_bend output in hybrid/dex mode")
    ap.add_argument("--flip-thumb-rot", action="store_true",
                    help="invert dex thumb_rot output in hybrid/dex mode")
    args = ap.parse_args()

    log   = get_logger("mimic", log_dir=LOG_DIR)
    calib = load_calibration(args.config, log)

    if not args.model.exists():
        log.error("model not found: %s", args.model); return 1

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

    # dex-retargeting backend, loaded for 'dex' (full) and 'hybrid' (pinch only)
    dex_ret = None
    _dex_open_path = PROJECT_ROOT / "config" / "dex_open.yaml"
    if args.backend in ("dex", "hybrid"):
        from dexkit.mapping.dex_backend import DexRetarget
        # hybrid uses dex only for the pinch -> DexPilot gives the best pinch
        # and its finger-lift is irrelevant (we keep other fingers from geom).
        use_dp = args.dexpilot or args.backend == "hybrid"
        dex_ret = {"right": DexRetarget("right", low_pass_alpha=args.dex_alpha, use_dexpilot=use_dp),
                   "left":  DexRetarget("left",  low_pass_alpha=args.dex_alpha, use_dexpilot=use_dp)}
        if args.backend == "dex" and _dex_open_path.exists():
            d = yaml.safe_load(_dex_open_path.read_text()) or {}
            for k, dr in dex_ret.items():
                if k in d:
                    dr.set_open_q(d[k])
            log.info("loaded dex open refs from %s", _dex_open_path)
        log.info("backend=%s (dex %s)", args.backend,
                 "dexpilot" if use_dp else "vector")
    else:
        log.info("backend=geom (calibrated geometric mapping)")

    def make_smoother():
        if getattr(calib, "pinch_alpha", None) and len(calib.pinch_alpha) == 6:
            a = list(calib.pinch_alpha)
            a[4] = args.thumb_alpha   # force thumb_bend to flag value (no config override)
            a[5] = args.thumb_alpha   # force thumb_rot to flag value
            return AngleSmoother(alpha=a)
        return AngleSmoother(alpha=[args.alpha]*4 + [args.thumb_alpha]*2)

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
                channels[side] = {"hand": hand, "smoother": make_smoother(),
                                   "last_sent": None, "last_cmd": 0.0}
                log.info("connected %s port=%s", side, port)
            except Exception:
                log.exception("failed to open %s on %s", side, port)

    if not channels:
        for side in ("left", "right"):
            channels[side] = {"hand": None, "smoother": make_smoother(),
                               "last_sent": None, "last_cmd": 0.0}
        log.info("no hands — perception only")

    # Pinch threshold only needed for hybrid mode
    pinch_threshold = 0.5262
    pinch_samples: list[float] = []
    PINCH_SAMPLES_NEEDED = 5
    if args.backend in ("hybrid", "dex"):
        _cfg_path = PROJECT_ROOT / "config" / "hand_mimic.yaml"
        _cfg_data = yaml.safe_load(_cfg_path.read_text()) if _cfg_path.exists() else {}
        pinch_threshold = float(
            (_cfg_data.get("calibration") or {}).get("pinch_threshold", 0.5262)
        )

    min_dt = 1.0 / args.fps if args.fps > 0 else 0.0
    frame_i    = 0
    win        = "hand_mimic"
    fullscreen = args.fullscreen

    if not args.no_window:
        hint = "q=quit  f=fullscreen  c=calibrate  t=reload"
        if args.backend in ("hybrid", "dex"):
            hint += "  P=pinch sample x5  o=set open refs"
        print(hint)
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        try:
            w, h = (int(v) for v in args.window_size.lower().split("x"))
            cv2.resizeWindow(win, w, h)
        except Exception:
            cv2.resizeWindow(win, 1280, 960)
        if fullscreen:
            cv2.setWindowProperty(win, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    try:
        while True:
            ok, frame = source.read()
            if not ok:
                log.error("frame read failed"); break
            frame_i += 1
            if args.mirror:
                frame = cv2.flip(frame, 1)

            result = tracker.process(frame)
            draw_landmarks(frame, result)

            seen: set[str] = set()
            now = time.monotonic()

            for i in range(result.num_hands):
                label = result.handedness[i] if i < len(result.handedness) else "?"
                side  = _physical_side(label, args.swap_hands)   # physical routing
                # Rotation chirality follows the CALIBRATION convention (swap on),
                # decoupled from --no-swap-hands so toggling routing never flips rot.
                rot_side = _physical_side(label, True)
                ch    = channels.get(side)
                if ch is None:
                    continue
                seen.add(side)

                lm = result.landmarks[i]

                # Hand state detection
                hand_state = None
                if args.backend in ("hybrid", "dex"):
                    hand_state = _hand_state(lm, pinch_threshold)
                is_fist    = hand_state == "fist"
                is_3finger = hand_state == "3pinch"
                is_pinch   = hand_state == "2pinch"

                def _dex_angles():
                    dr = dex_ret.get(label.lower())
                    if dr is None:
                        return None
                    wl = (result.world_landmarks[i]
                          if result.world_landmarks and i < len(result.world_landmarks)
                          else lm)
                    a = list(dr.retarget(wl))
                    if args.flip_thumb_bend:
                        a[4] = 1000 - a[4]
                    if args.flip_thumb_rot:
                        a[5] = 1000 - a[5]
                    return a

                if args.backend == "dex":
                    # Full dex-retargeting
                    try:
                        da = _dex_angles()
                    except Exception:
                        log.exception("dex retarget failed (%s)", side); continue
                    if da is None:
                        continue
                    angles = da
                else:
                    # geom base (full open, calibrated); hybrid overrides
                    # index+thumb with dex ONLY during a pinch.
                    try:
                        raw, _ = landmarks_to_angles(lm, calib, hand_side=rot_side)
                    except Exception:
                        log.exception("retarget failed (%s)", side); continue
                    if args.backend == "hybrid" and dex_ret is not None:
                        try:
                            da = _dex_angles()
                            if da is not None:
                                raw = list(raw)
                                if is_3finger:
                                    raw[2] = da[2]              # middle
                                    raw[3] = da[3]              # index
                                    raw[4] = da[4]              # thumb_bend
                                    raw[5] = max(0, min(1000, da[5] + args.pinch_rot_offset // 2))
                                elif is_pinch:
                                    raw[3] = da[3]              # index
                                    raw[4] = da[4]              # thumb_bend
                                    raw[5] = max(0, min(1000, da[5] + args.pinch_rot_offset))
                        except Exception:
                            log.exception("hybrid dex override failed (%s)", side)

                    angles = ch["smoother"].update(raw)

                # Dots: orange=fist, blue=3-finger pinch, red=2-finger pinch
                fh, fw = frame.shape[:2]
                if is_fist:
                    for tip in (4, 8, 12, 16, 20):
                        tx, ty = int(lm[tip][0] * fw), int(lm[tip][1] * fh)
                        cv2.circle(frame, (tx, ty), 9, (0, 100, 255), -1)  # orange
                elif is_3finger:
                    for tip in (4, 8, 12):
                        tx, ty = int(lm[tip][0] * fw), int(lm[tip][1] * fh)
                        cv2.circle(frame, (tx, ty), 9, (255, 100, 0), -1)  # blue
                elif is_pinch:
                    for tip in (4, 8):
                        tx, ty = int(lm[tip][0] * fw), int(lm[tip][1] * fh)
                        cv2.circle(frame, (tx, ty), 9, (0, 0, 255), -1)  # red

                tag = "[FIST]" if is_fist else ("[3-PINCH]" if is_3finger else ("[PINCH]" if is_pinch else ""))
                disp_label = "Right" if label == "Left" else ("Left" if label == "Right" else label)
                cv2.putText(frame, f"{disp_label}{tag}: {angles}",
                            (10, 30 + 26 * len(seen)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)

                if now - ch["last_cmd"] >= min_dt:
                    out, changed = _apply_deadband(angles, ch["last_sent"], args.deadband)
                    if changed:
                        ch["last_cmd"]  = now
                        ch["last_sent"] = out
                        if ch["hand"] is not None:
                            ch["hand"].set_angles(out, wait=False)
                        else:
                            print(f"[dry-run] {side}: {out}", end="\r")

            for side, ch in channels.items():
                if side not in seen:
                    ch["smoother"].reset()
                    ch["last_sent"] = None

            if not args.no_window:
                cv2.putText(frame, "c=calibrate  t=reload  P=pinch sample  f=fullscreen  q=quit",
                            (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (200, 200, 200), 1)

            if args.no_window:
                if frame_i >= args.max_frames:
                    break
            else:
                cv2.imshow(win, frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("f"):
                    fullscreen = not fullscreen
                    cv2.setWindowProperty(
                        win, cv2.WND_PROP_FULLSCREEN,
                        cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
                if key == ord("c") or key == ord("C"):
                    # Launch guided range calibration in-place, then hot-reload
                    log.info("entering calibration from mimic")
                    new_calib = run_calibration(
                        source, tracker, win,
                        mirror=args.mirror, swap_hands=args.swap_hands, log=log,
                        calib=calib)
                    calib, pinch_threshold = _reload(args.config, log)
                    for ch_ in channels.values():
                        ch_["smoother"] = make_smoother()
                        ch_["last_sent"] = None
                    log.info("calibration %s — resumed mimic",
                             "applied" if new_calib else "cancelled")
                if key == ord("t") or key == ord("T"):
                    calib, pinch_threshold = _reload(args.config, log)
                    for ch_ in channels.values():
                        ch_["smoother"] = make_smoother()
                        ch_["last_sent"] = None
                    log.info("hot-reloaded config (T key)")
                if (key == ord("o") or key == ord("O")) and dex_ret is not None:
                    # Capture current OPEN-hand qpos per finger -> map to max(1000)
                    captured = {}
                    for ri in range(result.num_hands):
                        rl = (result.handedness[ri].lower()
                              if ri < len(result.handedness) else "")
                        dr = dex_ret.get(rl)
                        if dr is not None:
                            dr.set_open_q(dr.last_q6, scale=0.8)  # ring/pinky, partial
                            captured[rl] = [float(round(x, 4)) for x in dr.open_q[:4]]
                    if captured:
                        d = yaml.safe_load(_dex_open_path.read_text()) if _dex_open_path.exists() else {}
                        d = d or {}
                        d.update(captured)
                        _dex_open_path.write_text(yaml.dump(d, default_flow_style=False))
                        print(f"  dex open refs set + saved: {list(captured)}")
                        log.info("dex open refs captured: %s", captured)
                if (key == ord("p") or key == ord("P")) and args.backend in ("hybrid", "dex"):
                    sample_dist = None
                    for ri in range(result.num_hands):
                        rl = result.handedness[ri] if ri < len(result.handedness) else ""
                        if _physical_side(rl, args.swap_hands) == "right":
                            sample_dist = _pinch_dist(result.landmarks[ri])
                            break
                    if sample_dist is None and result.num_hands:
                        sample_dist = _pinch_dist(result.landmarks[0])
                    if sample_dist is not None:
                        pinch_samples.append(sample_dist)
                        n = len(pinch_samples)
                        print(f"  sample {n}/{PINCH_SAMPLES_NEEDED}: dist={sample_dist:.4f}")
                        if n >= PINCH_SAMPLES_NEEDED:
                            pinch_threshold = float(np.mean(pinch_samples)) * 1.2
                            pinch_samples.clear()
                            _cfg_path2 = PROJECT_ROOT / "config" / "hand_mimic.yaml"
                            _cfg2 = yaml.safe_load(_cfg_path2.read_text()) if _cfg_path2.exists() else {}
                            _cfg2.setdefault("calibration", {})["pinch_threshold"] = round(pinch_threshold, 4)
                            _cfg_path2.write_text(yaml.dump(_cfg2, default_flow_style=False, sort_keys=False))
                            print(f"  pinch threshold set to {pinch_threshold:.4f}")

            if pinch_samples and args.backend in ("hybrid", "dex"):
                cv2.putText(frame, f"[P] pinch samples: {len(pinch_samples)}/{PINCH_SAMPLES_NEEDED}",
                            (10, frame.shape[0] - 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 165, 255), 2)

    except Exception:
        log.exception("mimic loop crashed"); return 1
    finally:
        for ch in channels.values():
            if ch["hand"] is not None:
                ch["hand"].close()
        tracker.close()
        source.release()
        cv2.destroyAllWindows()
        log.info("mimic closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
