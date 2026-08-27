#!/usr/bin/env python3
"""inference_arm.py — runtime side of the G-1 arm-direction MLP.

``run_geoff_gui.py`` imports two names from this module::

    from data.inference_arm import predict_end_positions, load_bundle

* :func:`load_bundle` loads a ``.joblib`` bundle written by ``data/train.py``.
* :func:`predict_end_positions` maps a high-level direction + the current start
  joint angles to the predicted end joint angles for one arm.

The bundle is self-describing (it stores the direction one-hot order, the arm
joint indices, and the input/output scalers) so this module never needs to know
the training details — it just reproduces the same feature layout.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

# Kept in sync with train.py; only used as a fallback if a bundle predates the
# stored ``directions`` list.
DIRECTIONS = ["forward", "back", "left", "right", "up", "down"]

_ARTIFACTS = Path("data") / "artifacts"


def _default_bundle_path() -> Path:
    """Best-effort default when the GUI calls ``load_bundle()`` with no path.

    Prefer the left arm, then the right, then any bundle we can find.
    """
    for arm in ("left", "right"):
        p = _ARTIFACTS / f"{arm}-arm" / "arm_mlp.joblib"
        if p.exists():
            return p
    hits = sorted(_ARTIFACTS.glob("*/arm_mlp.joblib"))
    if hits:
        return hits[0]
    # Return the canonical left path so the error message is actionable.
    return _ARTIFACTS / "left-arm" / "arm_mlp.joblib"


def load_bundle(path: Optional[Path | str] = None) -> dict:
    """Load a trained bundle dict from ``path`` (or a sensible default).

    Raises ``FileNotFoundError`` with a hint if nothing has been trained yet.
    """
    import joblib

    bundle_path = Path(path) if path is not None else _default_bundle_path()
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"No trained arm model at {bundle_path}. "
            f"Record samples then run:  python3 data/train.py --arm left"
        )
    return joblib.load(bundle_path)


def _one_hot(direction: str, directions: Sequence[str]) -> np.ndarray:
    vec = np.zeros(len(directions), dtype=float)
    if direction in directions:
        vec[list(directions).index(direction)] = 1.0
    return vec


def predict_end_positions(
    direction: str,
    start_joints: Iterable[float],
    arm: str = "left",
    bundle: Optional[dict] = None,
) -> list[float]:
    """Predict end joint angles for ``arm`` given ``direction`` + ``start_joints``.

    Parameters
    ----------
    direction    : one of the trained directions (up/down/left/right/forward/back)
    start_joints : current arm-joint angles, ordered by ascending motor index
                   (exactly ``[joint_cur[i] for i in sorted(arm_joint_idx)]``)
    arm          : "left" | "right" — informational; the bundle is authoritative
    bundle       : a dict from :func:`load_bundle`; loaded on demand if omitted

    Returns
    -------
    list[float] : predicted end joint angles, same order/length as start_joints.
    """
    if bundle is None:
        bundle = load_bundle()

    start = np.asarray(list(start_joints), dtype=float).ravel()
    directions = bundle.get("directions", DIRECTIONS)

    # Unknown direction → no-op (hold current pose) rather than a wild guess.
    if direction not in directions:
        return start.tolist()

    n_expected = len(bundle["arm_joint_idx"])
    if start.shape[0] != n_expected:
        # Pad/trim defensively so a mismatch never crashes the GUI.
        fixed = np.zeros(n_expected, dtype=float)
        fixed[: min(n_expected, start.shape[0])] = start[:n_expected]
        start = fixed

    x = np.hstack([_one_hot(direction, directions), start]).reshape(1, -1)
    x = bundle["x_scaler"].transform(x)
    y = bundle["model"].predict(x)
    y = bundle["y_scaler"].inverse_transform(np.asarray(y).reshape(1, -1))
    return y.ravel().astype(float).tolist()


# ---------------------------------------------------------------------------
# Averaged-delta model (see data/build_arm_deltas.py)
# ---------------------------------------------------------------------------


def _default_deltas_path(arm: str = "left") -> Path:
    p = _ARTIFACTS / f"{arm}-arm" / "arm_deltas.joblib"
    if p.exists():
        return p
    hits = sorted(_ARTIFACTS.glob("*/arm_deltas.joblib"))
    return hits[0] if hits else p


def load_deltas(path: Optional[Path | str] = None, arm: str = "left") -> dict:
    """Load an averaged-delta bundle written by ``build_arm_deltas.py``."""
    import joblib

    bundle_path = Path(path) if path is not None else _default_deltas_path(arm)
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"No delta model at {bundle_path}. Build it with:  "
            f"python3 data/build_arm_deltas.py --arm {arm}"
        )
    return joblib.load(bundle_path)


def predict_delta_target(
    direction: str,
    current_joints: Iterable[float],
    gain: float = 1.0,
    bundle: Optional[dict] = None,
    arm: str = "left",
) -> list[float]:
    """Return ``current_joints + gain * delta[direction]`` for the averaged model.

    Parameters
    ----------
    direction      : one of the trained directions (up/down/left/right/forward/back)
    current_joints : current arm-joint angles, ascending motor-index order
    gain           : scale on the averaged movement (1.0 = full averaged delta)
    bundle         : a dict from :func:`load_deltas`; loaded on demand if omitted
    arm            : used only to locate the default bundle

    Unknown direction → returns the current pose unchanged (no-op).
    """
    if bundle is None:
        bundle = load_deltas(arm=arm)

    cur = np.asarray(list(current_joints), dtype=float).ravel()
    deltas = bundle.get("deltas", {})

    if direction not in deltas:
        return cur.tolist()

    delta = np.asarray(deltas[direction], dtype=float).ravel()

    # Defensive length match so a mismatch never crashes the GUI.
    n = cur.shape[0]
    if delta.shape[0] != n:
        fixed = np.zeros(n, dtype=float)
        fixed[: min(n, delta.shape[0])] = delta[:n]
        delta = fixed

    target = cur + gain * delta

    # Safety clamp to the range observed during recording (if present).
    q_min = bundle.get("q_min")
    q_max = bundle.get("q_max")
    if q_min is not None and q_max is not None:
        lo = np.asarray(q_min, dtype=float)[:n]
        hi = np.asarray(q_max, dtype=float)[:n]
        target = np.clip(target, lo, hi)

    return target.astype(float).tolist()


# ---------------------------------------------------------------------------
# Trajectory model (see data/build_arm_traj.py)
# ---------------------------------------------------------------------------


def _default_traj_path(arm: str = "left") -> Path:
    p = _ARTIFACTS / f"{arm}-arm" / "arm_traj.joblib"
    if p.exists():
        return p
    hits = sorted(_ARTIFACTS.glob("*/arm_traj.joblib"))
    return hits[0] if hits else p


def load_traj(path: Optional[Path | str] = None, arm: str = "left") -> dict:
    """Load a trajectory bundle written by ``build_arm_traj.py``.

    Raises ``FileNotFoundError`` with a hint if none has been built yet.
    """
    import joblib

    bundle_path = Path(path) if path is not None else _default_traj_path(arm)
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"No trajectory model at {bundle_path}. Build it with:  "
            f"python3 data/build_arm_traj.py --arm {arm}"
        )
    return joblib.load(bundle_path)


def predict_delta_trajectory(
    direction: str,
    bundle: dict,
) -> list[list[float]]:
    """Return the list of relative-delta frames for ``direction``.

    Each frame is a length-7 vector ``D[i]`` such that the runtime plays
    ``target[i] = current_pose + D[i]`` (with ``D[0] == 0`` — the arm starts
    from wherever it currently is and reproduces the crafted joint coordination).

    Unknown direction (or a direction with no recorded samples) → ``[]`` (no-op).
    """
    traj = bundle.get("traj", {})
    frames = traj.get(direction)
    if not frames:
        return []
    return [[float(v) for v in frame] for frame in frames]


# ---------------------------------------------------------------------------
# Continuous-command policy (see data/build_preset_policy.py)
# ---------------------------------------------------------------------------

# Direction → 3-axis unit command. MUST match build_preset_policy.AXIS_MAP.
POLICY_AXIS_MAP: dict[str, tuple[float, float, float]] = {
    "forward": (1.0, 0.0, 0.0),
    "back":    (-1.0, 0.0, 0.0),
    "right":   (0.0, 1.0, 0.0),
    "left":    (0.0, -1.0, 0.0),
    "up":      (0.0, 0.0, 1.0),
    "down":    (0.0, 0.0, -1.0),
}


def _default_policy_path(arm: str = "left") -> Path:
    p = _ARTIFACTS / f"{arm}-arm" / "arm_policy.joblib"
    if p.exists():
        return p
    hits = sorted(_ARTIFACTS.glob("*/arm_policy.joblib"))
    return hits[0] if hits else p


def load_policy(path: Optional[Path | str] = None, arm: str = "left") -> dict:
    """Load a continuous-command policy bundle written by ``build_preset_policy.py``."""
    import joblib

    bundle_path = Path(path) if path is not None else _default_policy_path(arm)
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"No policy model at {bundle_path}. Build it with:  "
            f"python3 data/build_preset_policy.py --arm {arm}"
        )
    return joblib.load(bundle_path)


def command_to_axes(command, bundle: Optional[dict] = None) -> np.ndarray:
    """Normalise ``command`` to a 3-axis vector ``(fb, lr, ud)``.

    Accepts either:
      * a length-3 sequence ``(fb, lr, ud)`` — returned as-is, or
      * a dict of direction weights, e.g. ``{"forward": 0.7, "up": 0.3}`` —
        summed through the axis map (``back``/``left``/``down`` subtract).
    """
    axis_map = (bundle or {}).get("axis_map", POLICY_AXIS_MAP)
    if isinstance(command, dict):
        vec = np.zeros(3, dtype=float)
        for name, w in command.items():
            a = axis_map.get(name)
            if a is not None:
                vec += float(w) * np.asarray(a, dtype=float)
        return vec
    vec = np.asarray(list(command), dtype=float).ravel()
    if vec.shape[0] != 3:
        fixed = np.zeros(3, dtype=float)
        fixed[: min(3, vec.shape[0])] = vec[:3]
        vec = fixed
    return vec


def predict_policy_target(
    command,
    current_joints: Iterable[float],
    arm: str = "left",
    bundle: Optional[dict] = None,
    gain: float = 1.0,
) -> list[float]:
    """Map a continuous command + current pose to a blended target pose.

    Parameters
    ----------
    command        : a 3-vector ``(fb, lr, ud)`` (each ~[-1, 1]) OR a dict of
                     direction weights, e.g. ``{"forward": 0.7, "up": 0.3}``.
    current_joints : current arm-joint angles, ascending motor-index order.
    arm            : used only to locate the default bundle.
    bundle         : a dict from :func:`load_policy`; loaded on demand if omitted.
    gain           : scale on the predicted delta (1.0 = full).

    Returns
    -------
    list[float] : target joint angles = ``current + gain * delta(command)``,
                  clamped to the demonstrated range and the URDF joint limits.
    """
    if bundle is None:
        bundle = load_policy(arm=arm)

    cur = np.asarray(list(current_joints), dtype=float).ravel()
    n = len(bundle.get("home", cur))

    # Defensive length match on the current pose.
    if cur.shape[0] != n:
        fixed = np.zeros(n, dtype=float)
        fixed[: min(n, cur.shape[0])] = cur[:n]
        cur = fixed

    x = command_to_axes(command, bundle).reshape(1, -1)
    x = bundle["x_scaler"].transform(x)
    delta = bundle["y_scaler"].inverse_transform(
        np.asarray(bundle["model"].predict(x)).reshape(1, -1)
    ).ravel()

    target = cur + gain * delta

    # Soft clamp to the demonstrated range, then hard clamp to URDF limits.
    q_min, q_max = bundle.get("q_min"), bundle.get("q_max")
    if q_min is not None and q_max is not None:
        target = np.clip(target, np.asarray(q_min)[:n], np.asarray(q_max)[:n])
    limits = bundle.get("limits")
    if limits is not None:
        lo = np.asarray([l for l, _ in limits], dtype=float)[:n]
        hi = np.asarray([h for _, h in limits], dtype=float)[:n]
        target = np.clip(target, lo, hi)

    return target.astype(float).tolist()
