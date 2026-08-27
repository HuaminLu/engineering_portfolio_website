#!/usr/bin/env python3
"""train.py — fit the G-1 arm-direction MLP from recorded samples.

Reads the CSV produced by ``arm_train_recorder.py`` and trains a small
scikit-learn ``MLPRegressor`` that maps

    (direction one-hot, start arm-joint angles)  ->  end arm-joint angles

The trained model plus its scalers and metadata are pickled into a single
"bundle" that ``data/inference_arm.py`` (and therefore ``run_geoff_gui.py``)
loads at runtime.

Usage
=====
    # train the left arm from all good samples
    python3 data/train.py --arm left

    # only use your best takes
    python3 data/train.py --arm left --min-quality 0.6

    # only train on the most recent recording session
    python3 data/train.py --arm left --session latest

Input   : data/arms/<arm>/training_data_with_waist.csv
Output  : data/artifacts/<arm>-arm/arm_mlp.joblib

Notes
-----
* The **waist** columns (index 12) are recorded but *ignored* here — the model
  only learns the 7 arm joints, matching what ``run_geoff_gui.py`` feeds in and
  applies back out.
* Comment rows written by the recorder (lines starting with ``#``) are skipped.
* ``--min-quality`` / ``--session`` let you exclude bad takes *without* editing
  the CSV, so the always-append recorder never loses data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Direction one-hot order — MUST match the recorder and inference_arm.
DIRECTIONS = ["forward", "back", "left", "right", "up", "down"]

# Arm joint motor indices (waist index 12 is deliberately excluded).
LEFT_IDX = [15, 16, 17, 18, 19, 20, 21]
RIGHT_IDX = [22, 23, 24, 25, 26, 27, 28]


def _arm_indices(arm: str) -> list[int]:
    return LEFT_IDX if arm == "left" else RIGHT_IDX


def _default_csv(arm: str) -> Path:
    return Path("data") / "arms" / arm / "training_data_with_waist.csv"


def _default_out(arm: str) -> Path:
    return Path("data") / "artifacts" / f"{arm}-arm" / "arm_mlp.joblib"


def load_samples(csv_path: Path, arm: str, min_quality: float, session: str):
    """Return (X_dir, X_start, Y_end, meta) filtered per the CLI options.

    X_dir   : (N,) direction strings
    X_start : (N, 7) start arm-joint angles
    Y_end   : (N, 7) end arm-joint angles
    """
    import pandas as pd

    if not csv_path.exists():
        raise SystemExit(
            f"No training data at {csv_path}\n"
            f"Record some samples first with:  python3 arm_train_recorder.py --arm {arm}"
        )

    # comment='#' drops the recorder's session bracket rows.
    df = pd.read_csv(csv_path, comment="#")
    if df.empty:
        raise SystemExit(f"{csv_path} has a header but no samples yet.")

    arm_idx = _arm_indices(arm)
    start_cols = [f"start_{i}" for i in arm_idx]
    end_cols = [f"end_{i}" for i in arm_idx]

    missing = [c for c in (["direction"] + start_cols + end_cols) if c not in df.columns]
    if missing:
        raise SystemExit(
            f"{csv_path} is missing expected columns for the {arm} arm: {missing}\n"
            f"Was it recorded for a different arm?"
        )

    # keep only valid direction rows (defensive against stray/comment lines)
    df = df[df["direction"].isin(DIRECTIONS)].copy()

    n_before = len(df)

    # session filter
    if session != "all" and "session_id" in df.columns:
        sids = df["session_id"].dropna().astype(str)
        if session == "latest":
            if sids.empty:
                raise SystemExit("No session_id values found — cannot use --session latest.")
            chosen = sorted(sids.unique())[-1]
        else:
            chosen = session
        df = df[df["session_id"].astype(str) == chosen]
        print(f"[train] session filter → {chosen}: {len(df)}/{n_before} rows")

    # quality filter
    if min_quality > 0.0 and "quality" in df.columns:
        q = df["quality"].astype(float)
        df = df[q >= min_quality]
        print(f"[train] quality >= {min_quality}: {len(df)} rows kept")

    if df.empty:
        raise SystemExit("No samples left after filtering — loosen --min-quality / --session.")

    X_dir = df["direction"].to_numpy()
    X_start = df[start_cols].to_numpy(dtype=float)
    Y_end = df[end_cols].to_numpy(dtype=float)

    sessions = (
        sorted(df["session_id"].astype(str).unique())
        if "session_id" in df.columns
        else []
    )
    per_dir = {d: int((X_dir == d).sum()) for d in DIRECTIONS}
    meta = {"sessions": sessions, "per_direction": per_dir}
    return X_dir, X_start, Y_end, meta


def one_hot(directions: np.ndarray) -> np.ndarray:
    idx = {d: i for i, d in enumerate(DIRECTIONS)}
    out = np.zeros((len(directions), len(DIRECTIONS)), dtype=float)
    for r, d in enumerate(directions):
        out[r, idx[d]] = 1.0
    return out


def train(args) -> None:
    from sklearn.neural_network import MLPRegressor
    from sklearn.preprocessing import StandardScaler
    import joblib
    import sklearn

    arm = args.arm
    csv_path = Path(args.csv) if args.csv else _default_csv(arm)
    out_path = Path(args.out) if args.out else _default_out(arm)

    X_dir, X_start, Y_end, meta = load_samples(
        csv_path, arm, args.min_quality, args.session
    )
    n = len(X_dir)
    print(f"[train] {n} samples for {arm} arm from {csv_path}")
    for d in DIRECTIONS:
        print(f"          {d:<8s}: {meta['per_direction'][d]}")

    if n < 8:
        print(
            f"[train] WARNING: only {n} samples — the model will barely "
            f"generalise. Aim for ~30+ per direction."
        )

    # Features: direction one-hot ++ start joints.  Scale the joint block so the
    # MLP trains cleanly; the one-hot block is already 0/1.
    X = np.hstack([one_hot(X_dir), X_start])
    Y = Y_end

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

    # Report training fit (RMSE in radians on the raw targets).
    pred = y_scaler.inverse_transform(model.predict(Xs))
    rmse = float(np.sqrt(np.mean((pred - Y) ** 2)))
    print(f"[train] hidden={hidden} epochs={args.epochs} "
          f"final_loss={model.loss_:.5f} train_RMSE={rmse:.4f} rad")

    bundle = {
        "model": model,
        "x_scaler": x_scaler,
        "y_scaler": y_scaler,
        "directions": DIRECTIONS,
        "arm": arm,
        "arm_joint_idx": _arm_indices(arm),
        "n_samples": n,
        "per_direction": meta["per_direction"],
        "sessions": meta["sessions"],
        "min_quality": args.min_quality,
        "hidden": hidden,
        "epochs": args.epochs,
        "sklearn_version": sklearn.__version__,
        "feature_layout": "onehot(directions) ++ start_arm_joints",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_path)
    print(f"[train] saved bundle → {out_path}")
    print(f"[train] done. In the GUI, select the {arm.title()} arm and use the "
          f"arrow keys (+ f/b) to drive it.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Train the G-1 arm-direction MLP")
    ap.add_argument("--arm", choices=["left", "right"], default="left")
    ap.add_argument("--csv", default=None, help="override input CSV path")
    ap.add_argument("--out", default=None, help="override output .joblib path")
    ap.add_argument("--min-quality", type=float, default=0.0,
                    help="drop samples below this quality (0-1). default 0 = keep all")
    ap.add_argument("--session", default="all",
                    help="'all' (default), 'latest', or a specific session_id")
    ap.add_argument("--epochs", type=int, default=200, help="max training iterations")
    ap.add_argument("--hidden", default="32,32",
                    help="comma-separated hidden layer sizes (default 32,32)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    train(args)


if __name__ == "__main__":
    main()
