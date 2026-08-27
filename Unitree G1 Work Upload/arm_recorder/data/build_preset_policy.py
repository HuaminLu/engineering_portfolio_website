#!/usr/bin/env python3
"""build_preset_policy.py — turn the 6 recorded direction presets into a
continuous, blendable arm policy.

The arm recorder GUI (``arm_anim_recorder.py``) saves each direction preset as a
full frame sequence at::

    data/presets/<direction>/animation.yaml
        {gesture, fps, frames: {left: [[7]...], right: [[7]...]}}

Those presets are *discrete* — you can only replay a recorded direction. This
script turns them into a **continuous policy**: a small ``MLPRegressor`` (the same
sentdex approach as ``data/train.py``) that maps a **3-axis command** to a
**relative joint-space delta**, so a blended command like ``(0.7, 0, 0.3)``
("70% forward + 30% up") produces a single target pose *between* the presets.

Command axes (each component ~[-1, 1]):

    axis 0:  +forward / -back
    axis 1:  +right   / -left
    axis 2:  +up      / -down

Training pairs come from every frame of every preset: a frame at phase
``t = k/(N-1)`` of direction ``d`` teaches ``command = t * axis(d)  ->  frame - home``.
This gives proportional magnitude along each axis; the MLP interpolates blends
between axes. A ``(0,0,0) -> 0`` anchor pins the home pose.

Usage
=====
    python3 data/build_preset_policy.py --arm left
    python3 data/build_preset_policy.py --arm left --hidden 32,32 --epochs 400

Input   : data/presets/<direction>/animation.yaml   (from arm_anim_recorder.py)
Output  : data/artifacts/<arm>-arm/arm_policy.joblib
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

# Direction → 3-axis unit command. MUST match inference_arm.predict_policy_target.
AXIS_MAP: dict[str, tuple[float, float, float]] = {
    "forward": (1.0, 0.0, 0.0),
    "back":    (-1.0, 0.0, 0.0),
    "right":   (0.0, 1.0, 0.0),
    "left":    (0.0, -1.0, 0.0),
    "up":      (0.0, 0.0, 1.0),
    "down":    (0.0, 0.0, -1.0),
}
DIRECTIONS = list(AXIS_MAP.keys())

# Arm joint motor indices (matches train.py / build_arm_deltas.py).
LEFT_IDX = [15, 16, 17, 18, 19, 20, 21]
RIGHT_IDX = [22, 23, 24, 25, 26, 27, 28]

# Neutral home pose — every joint at 0 (matches arm_anim_recorder.HOME_POSE).
HOME_POSE = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

# Per-joint URDF limits (radians), from arm_anim_recorder.py. Only shoulder-roll
# differs L/R. Stored in the bundle so inference can hard-clamp.
LIMITS_LEFT = [
    (-3.089, 2.670), (-1.588, 2.252), (-2.618, 2.618), (-1.047, 2.094),
    (-1.972, 1.972), (-1.614, 1.614), (-1.614, 1.614),
]
LIMITS_RIGHT = [
    (-3.089, 2.670), (-2.252, 1.588), (-2.618, 2.618), (-1.047, 2.094),
    (-1.972, 1.972), (-1.614, 1.614), (-1.614, 1.614),
]


def _arm_idx(arm: str) -> list[int]:
    return LEFT_IDX if arm == "left" else RIGHT_IDX


def _limits(arm: str) -> list[tuple[float, float]]:
    return LIMITS_LEFT if arm == "left" else LIMITS_RIGHT


def _preset_path(direction: str) -> Path:
    return Path("data") / "presets" / direction / "animation.yaml"


def _default_out(arm: str) -> Path:
    return Path("data") / "artifacts" / f"{arm}-arm" / "arm_policy.joblib"


def _load_preset_frames(direction: str, arm: str) -> np.ndarray | None:
    """Return (N, 7) frames for ``arm`` from a preset, or None if unavailable."""
    import yaml

    p = _preset_path(direction)
    if not p.exists():
        return None
    data = yaml.safe_load(p.read_text()) or {}
    frames = (data.get("frames") or {}).get(arm)
    if not frames:
        return None
    arr = np.asarray(frames, dtype=float)
    if arr.ndim != 2 or arr.shape[1] != len(HOME_POSE):
        return None
    return arr


def build(args) -> None:
    from sklearn.neural_network import MLPRegressor
    from sklearn.preprocessing import StandardScaler
    import joblib
    import sklearn

    arm = args.arm
    home = np.asarray(HOME_POSE, dtype=float)
    out_path = Path(args.out) if args.out else _default_out(arm)

    X_rows: list[list[float]] = []
    Y_rows: list[list[float]] = []
    per_dir_mag: dict[str, float] = {}
    per_dir_frames: dict[str, int] = {}
    used_dirs: list[str] = []
    all_poses: list[np.ndarray] = [home.copy()]  # for observed-range clamp

    # Anchor: zero command holds the home pose.
    X_rows.append([0.0, 0.0, 0.0])
    Y_rows.append([0.0] * len(HOME_POSE))

    print(f"[policy] loading presets for the {arm} arm from data/presets/<dir>/animation.yaml")
    for d in DIRECTIONS:
        frames = _load_preset_frames(d, arm)
        if frames is None:
            print(f"          {d:<8s}: MISSING — skipped")
            continue
        n = len(frames)
        axis = np.asarray(AXIS_MAP[d], dtype=float)
        for k, f in enumerate(frames):
            t = k / (n - 1) if n > 1 else 1.0
            X_rows.append((t * axis).tolist())
            Y_rows.append((f - home).tolist())
            all_poses.append(f)
        end_mag = float(np.linalg.norm(frames[-1] - home))
        per_dir_mag[d] = end_mag
        per_dir_frames[d] = n
        used_dirs.append(d)
        print(f"          {d:<8s}: n={n:<4d} |end-home|={end_mag:.3f} rad")

    if not used_dirs:
        raise SystemExit(
            "No presets found. Record the 6 directions in arm_anim_recorder.py "
            "(Save Preset for up/down/left/right/forward/back) first."
        )
    if len(used_dirs) < len(DIRECTIONS):
        missing = [d for d in DIRECTIONS if d not in used_dirs]
        print(f"[policy] WARNING: training on a partial set — missing {missing}. "
              f"Commands along those axes will be weak/zero.")

    X = np.asarray(X_rows, dtype=float)
    Y = np.asarray(Y_rows, dtype=float)
    print(f"[policy] {len(X)} training pairs from {len(used_dirs)} direction(s)")

    x_scaler = StandardScaler().fit(X)
    y_scaler = StandardScaler().fit(Y)
    Xs = x_scaler.transform(X)
    Ys = y_scaler.transform(Y)

    hidden = tuple(int(h) for h in args.hidden.split(",") if h.strip())
    model = MLPRegressor(
        hidden_layer_sizes=hidden,
        activation="relu",
        solver="adam",
        max_iter=args.epochs,
        random_state=args.seed,
        early_stopping=False,
    )
    model.fit(Xs, Ys)

    pred = y_scaler.inverse_transform(model.predict(Xs))
    rmse = float(np.sqrt(np.mean((pred - Y) ** 2)))
    print(f"[policy] hidden={hidden} epochs={args.epochs} "
          f"final_loss={model.loss_:.5f} train_RMSE={rmse:.4f} rad")

    # Observed per-joint range across every demonstrated pose (safety clamp).
    poses = np.vstack(all_poses)
    q_min = poses.min(axis=0).tolist()
    q_max = poses.max(axis=0).tolist()

    bundle = {
        "type": "command_policy",
        "model": model,
        "x_scaler": x_scaler,
        "y_scaler": y_scaler,
        "arm": arm,
        "arm_joint_idx": _arm_idx(arm),
        "axis_map": AXIS_MAP,
        "axis_order": ["forward/back", "right/left", "up/down"],
        "directions": used_dirs,
        "home": HOME_POSE,
        "limits": _limits(arm),           # URDF hard limits (per joint)
        "q_min": q_min,                   # observed soft clamp
        "q_max": q_max,
        "per_direction_end_mag": per_dir_mag,
        "per_direction_frames": per_dir_frames,
        "hidden": hidden,
        "epochs": args.epochs,
        "sklearn_version": sklearn.__version__,
        "feature_layout": "command(3: fb,lr,ud) -> delta_from_home(7); target = current + gain*delta",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_path)
    print(f"[policy] saved bundle -> {out_path}")
    print(f"[policy] try:  python3 data/policy_demo.py --arm {arm} --cmd 0.5,0,0.5")


def main() -> None:
    ap = argparse.ArgumentParser(description="Train a continuous blendable arm policy from GUI presets")
    ap.add_argument("--arm", choices=["left", "right"], default="left")
    ap.add_argument("--out", default=None, help="override output .joblib path")
    ap.add_argument("--hidden", default="32,32",
                    help="comma-separated hidden layer sizes (default 32,32)")
    ap.add_argument("--epochs", type=int, default=400, help="max training iterations")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    build(args)


if __name__ == "__main__":
    main()
