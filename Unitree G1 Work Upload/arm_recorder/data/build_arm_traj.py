#!/usr/bin/env python3
"""build_arm_traj.py — average recorded arm trajectories into a playback bundle.

Companion to ``build_arm_deltas.py``, but for the full-trajectory workflow
produced by ``arm_anim_recorder.py``.  For each direction it:

* reads every sample (a full per-joint frame path from the home pose) out of
  ``data/trajectories/<arm>/<direction>.yaml``,
* resamples each sample to a common frame count,
* averages the samples element-wise into one canonical trajectory,
* converts it to **relative delta frames** ``D[i] = mean[i] - mean[0]`` so the
  runtime can replay it from whatever pose the arm is currently in.

Averaging cancels the small hand-to-hand variation between takes while keeping
the characteristic coordinated motion of each command.

    python3 data/build_arm_traj.py --arm left
    python3 data/build_arm_traj.py --arm left --frames 40

Input   : data/trajectories/<arm>/<direction>.yaml   (from arm_anim_recorder.py)
Output  : data/artifacts/<arm>-arm/arm_traj.joblib
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import yaml

DIRECTIONS = ["forward", "back", "left", "right", "up", "down"]
LEFT_IDX = [15, 16, 17, 18, 19, 20, 21]
RIGHT_IDX = [22, 23, 24, 25, 26, 27, 28]
NJ = 7
DEFAULT_FPS = 25


def _arm_idx(arm: str) -> list[int]:
    return LEFT_IDX if arm == "left" else RIGHT_IDX


def _traj_dir(arm: str) -> Path:
    return Path("data") / "trajectories" / arm


def _default_out(arm: str) -> Path:
    return Path("data") / "artifacts" / f"{arm}-arm" / "arm_traj.joblib"


def _resample(sample: np.ndarray, n: int) -> np.ndarray:
    """Resample an (m, 7) trajectory to (n, 7) by linear index interpolation."""
    m = sample.shape[0]
    if m == n:
        return sample
    if m == 1:
        return np.repeat(sample, n, axis=0)
    xs = np.linspace(0.0, m - 1, n)
    out = np.empty((n, sample.shape[1]), dtype=float)
    for j in range(sample.shape[1]):
        out[:, j] = np.interp(xs, np.arange(m), sample[:, j])
    return out


def build(args) -> None:
    import joblib

    arm = args.arm
    tdir = _traj_dir(arm)
    out_path = Path(args.out) if args.out else _default_out(arm)

    if not tdir.exists():
        raise SystemExit(
            f"No trajectory folder at {tdir}\n"
            f"Record samples first with:  python3 arm_anim_recorder.py --iface <nic>"
        )

    per_dir_traj: dict[str, list[list[float]]] = {}
    per_dir_count: dict[str, int] = {}
    home_seen: list[float] | None = None
    fps = DEFAULT_FPS
    all_poses: list[np.ndarray] = []

    print(f"[traj] building {arm} arm from {tdir}")
    for d in DIRECTIONS:
        p = tdir / f"{d}.yaml"
        if not p.exists():
            per_dir_traj[d] = []
            per_dir_count[d] = 0
            print(f"        {d:<8s}: no file")
            continue
        data = yaml.safe_load(p.read_text()) or {}
        samples = data.get("samples", [])
        fps = int(data.get("fps", fps))
        if data.get("home") is not None and home_seen is None:
            home_seen = [float(x) for x in data["home"]]
        if not samples:
            per_dir_traj[d] = []
            per_dir_count[d] = 0
            print(f"        {d:<8s}: file present, 0 samples")
            continue

        arrs = [np.asarray(s, dtype=float).reshape(-1, NJ) for s in samples
                if len(s) >= 1]
        arrs = [a for a in arrs if a.shape[0] >= 1]
        if not arrs:
            per_dir_traj[d] = []
            per_dir_count[d] = 0
            continue

        # common length: user override, else the median sample length
        if args.frames > 0:
            n = args.frames
        else:
            n = int(np.median([a.shape[0] for a in arrs]))
            n = max(2, n)

        resampled = np.stack([_resample(a, n) for a in arrs], axis=0)  # (S, n, 7)
        mean_traj = resampled.mean(axis=0)                             # (n, 7)
        all_poses.append(mean_traj)

        # relative delta trajectory (pose-independent playback)
        delta = mean_traj - mean_traj[0:1, :]
        per_dir_traj[d] = delta.tolist()
        per_dir_count[d] = len(arrs)
        span = float(np.linalg.norm(mean_traj[-1] - mean_traj[0]))
        print(f"        {d:<8s}: n_samples={len(arrs):<3d} frames={n:<3d} "
              f"|end-start|={span:.3f} rad")

    if home_seen is None:
        home_seen = [0.0, 0.0, 0.0, 1.5708, 0.0, 0.0, 0.0]

    if all_poses:
        stacked = np.vstack(all_poses)
        q_min = stacked.min(axis=0).tolist()
        q_max = stacked.max(axis=0).tolist()
    else:
        q_min = q_max = None

    total = sum(per_dir_count.values())
    if total == 0:
        raise SystemExit("No samples found in any direction — record some first.")

    bundle = {
        "type": "traj",
        "arm": arm,
        "arm_joint_idx": _arm_idx(arm),
        "directions": DIRECTIONS,
        "fps": fps,
        "home": home_seen,
        "traj": per_dir_traj,        # direction -> [n_frames][7 relative deltas]
        "counts": per_dir_count,
        "q_min": q_min,
        "q_max": q_max,
        "n_samples": total,
        "feature_layout": "target[i] = current + traj[direction][i]  (clamped)",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out_path)
    print(f"[traj] saved bundle -> {out_path}  ({total} samples, fps={fps})")
    print(f"[traj] done. Play it in arm_gui.py — pressing a direction key replays "
          f"the {arm} arm trajectory.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Average recorded arm trajectories")
    ap.add_argument("--arm", choices=["left", "right"], default="left")
    ap.add_argument("--out", default=None, help="override output .joblib path")
    ap.add_argument("--frames", type=int, default=0,
                    help="resample every direction to this many frames "
                         "(0 = per-direction median length)")
    args = ap.parse_args()
    build(args)


if __name__ == "__main__":
    main()
