#!/usr/bin/env python3
"""train_er2.py — train the ER2 pixel-to-arm MLP.

Reads the CSV produced by ``arm_pixel_recorder.py`` and trains a small
scikit-learn ``MLPRegressor`` that maps

    (pixel_y, pixel_x, [depth_m,] start_arm_joints)  ->  end_arm_joints

where pixel_y / pixel_x are ER 2 normalized coordinates (0-1000, [y, x],
origin top-left).  The same input format drops directly out of
``gemini-robotics-er-2-preview``'s point query, so switching from the
recorded pixel to a live ER 2 point is zero-change at inference time.

If the CSV contains a ``depth_m`` column (recorded via ``arm_pixel_recorder.py
--lidar``), depth is used as a 3rd input feature, giving the model a true 3D
target coordinate.  Samples with a missing depth value are dropped when
``--use-depth`` is set; without that flag depth is ignored even if present.

Usage
=====
    # train the right arm (pixel only)
    python3 data/train_er2.py --arm right

    # train with depth (requires depth column in CSV)
    python3 data/train_er2.py --arm right --use-depth

    # only best samples
    python3 data/train_er2.py --arm right --min-quality 0.6

    # latest session only
    python3 data/train_er2.py --arm right --session latest

Input  : data/arms_pixel/<arm>/pixel_samples.csv
Output : data/artifacts/<arm>-arm/er2_mlp.joblib

Feature layout (pixel-only, 9 floats)
======================================
    [pixel_y/1000,  pixel_x/1000,  start_j0..j6]

Feature layout (with depth, 10 floats)
=======================================
    [pixel_y/1000,  pixel_x/1000,  depth_m,  start_j0..j6]

Both pixel dimensions are scaled to [0, 1] before the StandardScaler so they
sit in the same ballpark as joint angles (~[-3, 3] rad).  The scaler handles
the rest.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

LEFT_IDX  = [15, 16, 17, 18, 19, 20, 21]
RIGHT_IDX = [22, 23, 24, 25, 26, 27, 28]


def _arm_indices(arm: str) -> list[int]:
    return LEFT_IDX if arm == "left" else RIGHT_IDX


def _default_csv(arm: str) -> Path:
    return Path("data") / "arms_pixel" / arm / "pixel_samples.csv"


def _default_out(arm: str) -> Path:
    return Path("data") / "artifacts" / f"{arm}-arm" / "er2_mlp.joblib"


def load_samples(
    csv_path: Path, arm: str, min_quality: float, session: str, use_depth: bool
) -> tuple[np.ndarray, np.ndarray | None, np.ndarray, np.ndarray, dict]:
    """Return (X_pixel, X_depth_or_None, X_start, Y_end, meta).

    X_pixel        : (N, 2)  [[pixel_y, pixel_x], ...]  in 0-1000
    X_depth_or_None: (N, 1)  depth_m if ``use_depth`` and column present, else None
    X_start        : (N, 7)  start arm-joint angles
    Y_end          : (N, 7)  end arm-joint angles
    """
    import pandas as pd

    if not csv_path.exists():
        raise SystemExit(
            f"No pixel training data at {csv_path}\n"
            f"Record some samples first:  python3 arm_pixel_recorder.py --arm {arm}"
        )

    df = pd.read_csv(csv_path, comment="#")
    if df.empty:
        raise SystemExit(f"{csv_path} has a header but no samples yet.")

    idx = _arm_indices(arm)
    start_cols = [f"start_{i}" for i in idx]
    end_cols   = [f"end_{i}"   for i in idx]
    required   = ["pixel_y", "pixel_x"] + start_cols + end_cols
    missing    = [c for c in required if c not in df.columns]
    if missing:
        raise SystemExit(
            f"{csv_path} is missing expected columns: {missing}\n"
            f"Was it recorded with arm_pixel_recorder.py for the {arm} arm?"
        )

    n_before = len(df)

    if session != "all" and "session_id" in df.columns:
        sids = df["session_id"].dropna().astype(str)
        if session == "latest":
            if sids.empty:
                raise SystemExit("No session_id values — cannot use --session latest.")
            chosen = sorted(sids.unique())[-1]
        else:
            chosen = session
        df = df[df["session_id"].astype(str) == chosen]
        print(f"[train_er2] session filter → {chosen}: {len(df)}/{n_before} rows")

    if min_quality > 0.0 and "quality" in df.columns:
        q = df["quality"].astype(float)
        df = df[q >= min_quality]
        print(f"[train_er2] quality >= {min_quality}: {len(df)} rows kept")

    # Depth handling
    X_depth = None
    if use_depth:
        if "depth_m" not in df.columns:
            raise SystemExit(
                "--use-depth requested but CSV has no 'depth_m' column.\n"
                "Re-record samples with:  python3 arm_pixel_recorder.py --lidar"
            )
        n_with_depth = df["depth_m"].notna().sum()
        n_without = df["depth_m"].isna().sum()
        if n_without > 0:
            print(f"[train_er2] depth: dropping {n_without} samples with missing depth_m "
                  f"({n_with_depth} remain)")
            df = df[df["depth_m"].notna()]
        if df.empty:
            raise SystemExit("No samples with depth_m after filtering.")
        X_depth = df[["depth_m"]].to_numpy(dtype=float)

    if df.empty:
        raise SystemExit("No samples left after filtering.")

    X_pixel = df[["pixel_y", "pixel_x"]].to_numpy(dtype=float)
    X_start = df[start_cols].to_numpy(dtype=float)
    Y_end   = df[end_cols].to_numpy(dtype=float)

    sessions = (
        sorted(df["session_id"].astype(str).unique())
        if "session_id" in df.columns else []
    )
    py_min, py_max = float(X_pixel[:, 0].min()), float(X_pixel[:, 0].max())
    px_min, px_max = float(X_pixel[:, 1].min()), float(X_pixel[:, 1].max())
    meta = {
        "sessions": sessions,
        "pixel_y_range": (py_min, py_max),
        "pixel_x_range": (px_min, px_max),
        "use_depth": use_depth,
    }
    return X_pixel, X_depth, X_start, Y_end, meta


def train(args) -> None:
    from sklearn.neural_network import MLPRegressor
    from sklearn.preprocessing import StandardScaler
    import joblib
    import sklearn

    arm      = args.arm
    csv_path = Path(args.csv) if args.csv else _default_csv(arm)
    out_path = Path(args.out) if args.out else _default_out(arm)

    X_pixel, X_depth, X_start, Y_end, meta = load_samples(
        csv_path, arm, args.min_quality, args.session, args.use_depth
    )
    n = len(X_pixel)
    depth_tag = " +depth" if X_depth is not None else ""
    print(f"[train_er2] {n} samples for {arm} arm{depth_tag} from {csv_path}")
    print(f"[train_er2] pixel_y range: {meta['pixel_y_range'][0]:.0f}–{meta['pixel_y_range'][1]:.0f}")
    print(f"[train_er2] pixel_x range: {meta['pixel_x_range'][0]:.0f}–{meta['pixel_x_range'][1]:.0f}")

    if n < 20:
        print(
            f"[train_er2] WARNING: only {n} samples — model accuracy will be poor.\n"
            f"            Aim for 50+ samples covering the target workspace."
        )

    # Scale pixels to [0,1] first so they're on a similar scale to joint angles.
    # The StandardScaler then handles the rest of the normalisation.
    X_pixel_norm = X_pixel / 1000.0
    parts = [X_pixel_norm]
    if X_depth is not None:
        parts.append(X_depth)  # depth_m already in metres, scaler handles normalisation
    parts.append(X_start)
    X = np.hstack(parts)
    Y = Y_end

    feature_layout = "pixel_y/1000, pixel_x/1000"
    if X_depth is not None:
        feature_layout += ", depth_m"
    feature_layout += ", start_arm_joints[0..6]"

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
    print(
        f"[train_er2] hidden={hidden} epochs={args.epochs} "
        f"loss={model.loss_:.5f} train_RMSE={rmse:.4f} rad"
    )
    if rmse > 0.15:
        print(
            f"[train_er2] RMSE={rmse:.4f} rad is high — the model may not generalise.\n"
            f"            Record more samples or reduce the target workspace area."
        )

    bundle = {
        "model":          model,
        "x_scaler":       x_scaler,
        "y_scaler":       y_scaler,
        "arm":            arm,
        "arm_joint_idx":  _arm_indices(arm),
        "n_samples":      n,
        "hidden":         hidden,
        "epochs":         args.epochs,
        "pixel_y_range":  meta["pixel_y_range"],
        "pixel_x_range":  meta["pixel_x_range"],
        "use_depth":      X_depth is not None,
        "sessions":       meta["sessions"],
        "min_quality":    args.min_quality,
        "sklearn_version": sklearn.__version__,
        "feature_layout": feature_layout,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_path)
    print(f"[train_er2] saved bundle → {out_path}")
    print(
        f"[train_er2] run inference:  "
        f"python3 data/inference_er2.py --arm {arm} "
        f"--pixel-y 400 --pixel-x 500"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Train the ER2 pixel-to-arm MLP")
    ap.add_argument("--arm", choices=["left", "right"], default="right")
    ap.add_argument("--csv",  default=None, help="override input CSV path")
    ap.add_argument("--out",  default=None, help="override output .joblib path")
    ap.add_argument("--min-quality", type=float, default=0.0,
                    help="drop samples below this quality score (0-1)")
    ap.add_argument("--session", default="all",
                    help="'all' | 'latest' | <session_id>")
    ap.add_argument("--use-depth", action="store_true",
                    help="include depth_m as 3rd input feature (requires --lidar samples)")
    ap.add_argument("--epochs", type=int, default=500)
    ap.add_argument("--hidden", default="64,64",
                    help="hidden layer sizes (default 64,64; larger than direction MLP)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    train(args)


if __name__ == "__main__":
    main()
