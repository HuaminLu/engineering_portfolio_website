#!/usr/bin/env python3
"""inference_er2.py — runtime inference for the ER2 pixel-to-arm MLP.

Maps an ER 2 point output + current arm joints to predicted end joint angles:

    (pixel_y, pixel_x, [depth_m,] start_arm_joints)  ->  end_arm_joints

The pixel coordinate is in ER 2's native format: 0-1000 normalized [y, x],
origin top-left.  Pass it directly from the ``point`` field in er2_probe.py's
JSON output.

If the bundle was trained with ``--use-depth``, pass ``depth_m`` (metres, from
LidarDepthReader or a depth camera).  If depth is unavailable, pass ``None``
and inference falls back to predicting from pixel alone — accuracy will be
lower for depth-trained models but the call does not fail.

Public API (importable by run_geoff_gui.py or nova-voice tools)
==============================================================
    from data.inference_er2 import load_er2_bundle, predict_er2_target

    bundle = load_er2_bundle()
    joints = predict_er2_target(
        pixel_y=420, pixel_x=510,
        start_joints=reader.snapshot(arm_idx),
        bundle=bundle,
    )
    # joints is a list[float] of 7 arm joint angles ready for rt/arm_sdk.

CLI (quick sanity check without the robot)
==========================================
    python3 data/inference_er2.py --arm right --pixel-y 400 --pixel-x 500
    python3 data/inference_er2.py --arm right --pixel-y 400 --pixel-x 500 \\
        --start-joints 0.1 -0.2 0.0 0.5 0.0 0.1 0.0 --depth-m 0.82
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

_ARTIFACTS = Path("data") / "artifacts"

LEFT_IDX  = [15, 16, 17, 18, 19, 20, 21]
RIGHT_IDX = [22, 23, 24, 25, 26, 27, 28]


def _default_bundle_path(arm: str = "right") -> Path:
    p = _ARTIFACTS / f"{arm}-arm" / "er2_mlp.joblib"
    if p.exists():
        return p
    hits = sorted(_ARTIFACTS.glob("*/er2_mlp.joblib"))
    return hits[0] if hits else p


def load_er2_bundle(path: Optional[Path | str] = None, arm: str = "right") -> dict:
    """Load a trained ER2 bundle from ``path`` (or the default location).

    Raises ``FileNotFoundError`` if nothing has been trained yet.
    """
    import joblib

    bundle_path = Path(path) if path is not None else _default_bundle_path(arm)
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"No trained ER2 model at {bundle_path}.\n"
            f"Record samples then run:  python3 data/train_er2.py --arm {arm}"
        )
    return joblib.load(bundle_path)


def predict_er2_target(
    pixel_y: float,
    pixel_x: float,
    start_joints: Iterable[float],
    arm: str = "right",
    bundle: Optional[dict] = None,
    depth_m: Optional[float] = None,
) -> list[float]:
    """Predict end joint angles from an ER 2 point + current arm pose.

    Parameters
    ----------
    pixel_y, pixel_x : ER 2 normalized coordinates, 0-1000, origin top-left.
                       Pass the ``point`` field from er2_probe.py output directly.
    start_joints     : current arm-joint angles, ascending motor-index order
                       (exactly ``[joint_cur[i] for i in sorted(arm_joint_idx)]``).
    arm              : "left" | "right" — used only for default bundle lookup.
    bundle           : a dict from :func:`load_er2_bundle`; loaded on demand if omitted.
    depth_m          : metric depth from LiDAR or a depth camera (metres).
                       Required when the bundle was trained with ``use_depth=True``.
                       If None and the bundle uses depth, depth is set to 0.0 with
                       a warning — prediction will be less accurate.

    Returns
    -------
    list[float] : predicted end joint angles, same order/length as start_joints.
    """
    if bundle is None:
        bundle = load_er2_bundle(arm=arm)

    start = np.asarray(list(start_joints), dtype=float).ravel()

    n_expected = len(bundle["arm_joint_idx"])
    if start.shape[0] != n_expected:
        fixed = np.zeros(n_expected, dtype=float)
        fixed[: min(n_expected, start.shape[0])] = start[:n_expected]
        start = fixed

    # Build feature vector matching training layout.
    use_depth = bundle.get("use_depth", False)
    features: list[float] = [pixel_y / 1000.0, pixel_x / 1000.0]
    if use_depth:
        if depth_m is None:
            print(
                "[inference_er2] WARNING: bundle uses depth but depth_m=None — "
                "substituting 0.0; accuracy may be poor."
            )
            depth_m = 0.0
        features.append(float(depth_m))
    features.extend(start.tolist())

    x = np.asarray(features, dtype=float).reshape(1, -1)
    x = bundle["x_scaler"].transform(x)
    y = bundle["model"].predict(x)
    y = bundle["y_scaler"].inverse_transform(np.asarray(y).reshape(1, -1))
    return y.ravel().astype(float).tolist()


def _warn_coverage(pixel_y: float, pixel_x: float, bundle: dict) -> None:
    """Print a warning if the query pixel is outside the training coverage."""
    py_min, py_max = bundle.get("pixel_y_range", (0, 1000))
    px_min, px_max = bundle.get("pixel_x_range", (0, 1000))
    margin = 50  # 5% of 1000 — warn if notably outside the recorded workspace
    if pixel_y < py_min - margin or pixel_y > py_max + margin:
        print(
            f"[inference_er2] WARNING: pixel_y={pixel_y:.0f} is outside the "
            f"training range [{py_min:.0f}, {py_max:.0f}] — extrapolation may be inaccurate."
        )
    if pixel_x < px_min - margin or pixel_x > px_max + margin:
        print(
            f"[inference_er2] WARNING: pixel_x={pixel_x:.0f} is outside the "
            f"training range [{px_min:.0f}, {px_max:.0f}] — extrapolation may be inaccurate."
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Predict arm joints from an ER 2 pixel point",
        epilog=__doc__.split("CLI")[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--arm", choices=["left", "right"], default="right")
    ap.add_argument("--bundle", default=None, help="override bundle path")
    ap.add_argument("--pixel-y", type=float, required=True,
                    help="ER 2 pixel_y (0-1000)")
    ap.add_argument("--pixel-x", type=float, required=True,
                    help="ER 2 pixel_x (0-1000)")
    ap.add_argument("--depth-m", type=float, default=None,
                    help="metric depth in metres (required for depth-trained bundles)")
    ap.add_argument("--start-joints", type=float, nargs=7, default=None,
                    metavar="RAD",
                    help="7 start joint angles in radians (default: all zeros)")
    args = ap.parse_args()

    bundle = load_er2_bundle(args.bundle, args.arm)
    _warn_coverage(args.pixel_y, args.pixel_x, bundle)

    start = args.start_joints if args.start_joints is not None else [0.0] * 7
    result = predict_er2_target(
        args.pixel_y, args.pixel_x, start,
        arm=args.arm, bundle=bundle, depth_m=args.depth_m,
    )

    idx = bundle["arm_joint_idx"]
    print(f"pixel  → [y={args.pixel_y:.0f}, x={args.pixel_x:.0f}]")
    print(f"start  → {[f'{v:.3f}' for v in start]}")
    print(f"target → {[f'{v:.3f}' for v in result]}")
    print()
    print("joint targets (motor_idx: angle_rad):")
    for motor_idx, angle in zip(idx, result):
        print(f"  [{motor_idx:2d}]  {angle:+.4f} rad")

    print()
    print(f"[model] n_samples={bundle['n_samples']}  "
          f"hidden={bundle['hidden']}  "
          f"feature_layout={bundle['feature_layout']}")


if __name__ == "__main__":
    main()
