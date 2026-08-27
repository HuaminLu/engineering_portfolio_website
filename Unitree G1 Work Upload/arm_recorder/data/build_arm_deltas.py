#!/usr/bin/env python3
"""build_arm_deltas.py — averaged joint-space movements per direction.

Instead of an MLP that predicts absolute end poses (see ``train.py``), this
computes, for each high-level direction, the **mean joint-space displacement**
observed across all recorded samples::

    delta[direction] = mean_over_samples( end_joints - start_joints )

At runtime a keypress then nudges the arm from its *current* measured pose:

    target = current + gain * delta[direction]

Averaging cancels the per-sample noise in the hand-recorded data while keeping
the characteristic movement of each command.  Because it is relative, pressing a
key repeatedly keeps stepping the arm in that direction (matching the intended
IJKLUO tele-op feel), and it degrades gracefully from start poses never seen in
training.

    python3 data/build_arm_deltas.py --arm left
    python3 data/build_arm_deltas.py --arm left --min-quality 0.6

Input   : data/arms/<arm>/training_data_with_waist.csv   (cleaned/aligned)
Output  : data/artifacts/<arm>-arm/arm_deltas.joblib
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

DIRECTIONS = ["forward", "back", "left", "right", "up", "down"]
LEFT_IDX = [15, 16, 17, 18, 19, 20, 21]
RIGHT_IDX = [22, 23, 24, 25, 26, 27, 28]


def _arm_idx(arm: str) -> list[int]:
    return LEFT_IDX if arm == "left" else RIGHT_IDX


def _default_csv(arm: str) -> Path:
    return Path("data") / "arms" / arm / "training_data_with_waist.csv"


def _default_out(arm: str) -> Path:
    return Path("data") / "artifacts" / f"{arm}-arm" / "arm_deltas.joblib"


def build(args) -> None:
    import pandas as pd
    import joblib

    arm = args.arm
    csv_path = Path(args.csv) if args.csv else _default_csv(arm)
    out_path = Path(args.out) if args.out else _default_out(arm)

    if not csv_path.exists():
        raise SystemExit(
            f"No training data at {csv_path}\n"
            f"Record samples (arm_train_recorder.py) then clean (clean_data.py)."
        )

    df = pd.read_csv(csv_path, comment="#")
    if df.empty:
        raise SystemExit(f"{csv_path} has a header but no samples.")

    idx = _arm_idx(arm)
    start_cols = [f"start_{i}" for i in idx]
    end_cols = [f"end_{i}" for i in idx]
    missing = [c for c in (["direction"] + start_cols + end_cols) if c not in df.columns]
    if missing:
        raise SystemExit(
            f"{csv_path} is missing columns for the {arm} arm: {missing}\n"
            f"Run:  python3 data/clean_data.py --arm {arm}"
        )

    df = df[df["direction"].isin(DIRECTIONS)].copy()

    if args.min_quality > 0.0 and "quality" in df.columns:
        before = len(df)
        df = df[df["quality"].astype(float) >= args.min_quality]
        print(f"[deltas] quality >= {args.min_quality}: {len(df)}/{before} rows kept")

    if df.empty:
        raise SystemExit("No samples left after filtering — loosen --min-quality.")

    start = df[start_cols].to_numpy(dtype=float)
    end = df[end_cols].to_numpy(dtype=float)
    deltas_all = end - start  # (N, 7)

    # Observed per-joint range across every recorded start/end pose.  Used at
    # runtime to clamp targets so a full averaged delta can't drive a joint far
    # outside anything actually demonstrated.
    both = np.vstack([start, end])
    q_min = both.min(axis=0).tolist()
    q_max = both.max(axis=0).tolist()

    per_dir_delta: dict[str, list[float]] = {}
    per_dir_count: dict[str, int] = {}
    per_dir_std: dict[str, list[float]] = {}

    print(f"[deltas] {len(df)} samples for {arm} arm from {csv_path}")
    for d in DIRECTIONS:
        mask = (df["direction"] == d).to_numpy()
        n = int(mask.sum())
        per_dir_count[d] = n
        if n == 0:
            per_dir_delta[d] = [0.0] * len(idx)
            per_dir_std[d] = [0.0] * len(idx)
            print(f"          {d:<8s}: NO SAMPLES — delta = 0")
            continue
        mean_delta = deltas_all[mask].mean(axis=0)
        std_delta = deltas_all[mask].std(axis=0)
        per_dir_delta[d] = mean_delta.tolist()
        per_dir_std[d] = std_delta.tolist()
        mag = float(np.linalg.norm(mean_delta))
        noise = float(std_delta.mean())
        print(f"          {d:<8s}: n={n:<3d} |delta|={mag:.3f} rad  mean_std={noise:.3f}")

    bundle = {
        "type": "avg_delta",
        "arm": arm,
        "arm_joint_idx": idx,
        "directions": DIRECTIONS,
        "deltas": per_dir_delta,          # direction -> [7 joint deltas]
        "delta_std": per_dir_std,         # direction -> [7 joint stds]
        "counts": per_dir_count,
        "q_min": q_min,                   # observed per-joint min (safety clamp)
        "q_max": q_max,                   # observed per-joint max (safety clamp)
        "min_quality": args.min_quality,
        "n_samples": int(len(df)),
        "feature_layout": "target = current + gain * deltas[direction]",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_path)
    print(f"[deltas] saved bundle -> {out_path}")
    print(f"[deltas] done. Drive the {arm} arm in the GUI with I/K J/L U/O.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build averaged per-direction arm deltas")
    ap.add_argument("--arm", choices=["left", "right"], default="left")
    ap.add_argument("--csv", default=None, help="override input CSV path")
    ap.add_argument("--out", default=None, help="override output .joblib path")
    ap.add_argument("--min-quality", type=float, default=0.0,
                    help="drop samples below this quality (0-1). default 0 = keep all")
    args = ap.parse_args()
    build(args)


if __name__ == "__main__":
    main()
