#!/usr/bin/env python3
"""dex-retargeting backend — optimization-based hand retargeting.

Maps MediaPipe hand keypoints to Inspire RH56 joint angles using the
dex-retargeting library (DexPilot solver) + the Inspire URDF. Unlike the
geometric backend, it solves all DOFs jointly, so thumb-index coordination
(pinch) emerges from the optimizer rather than per-DOF calibration.

Output: 6 ints [pinky, ring, middle, index, thumb_bend, thumb_rot], 0-1000,
0=closed/bent 1000=open — same convention as the geometric backend.

Requires: torch (cpu ok), dex-retargeting, and the dex-urdf inspire assets.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

# MediaPipe-operator → MANO frame alignment (from dex-retargeting).
OPERATOR2MANO_RIGHT = np.array([[0, 0, -1], [-1, 0, 0], [0, 1, 0]], dtype=np.float32)
OPERATOR2MANO_LEFT  = np.array([[0, 0, -1], [1, 0, 0], [0, -1, 0]], dtype=np.float32)

# Default dex-urdf assets location (external clone).
DEFAULT_URDF_DIR = str(Path.home() / "Repo/geotree/dev/dex-urdf/robots/hands")

# URDF joint upper limits (rad) in target_joint_names order:
# [pinky, ring, middle, index, thumb_pitch(bend), thumb_yaw(rot)]
_UPPER = np.array([1.47, 1.47, 1.47, 1.47, 0.6, 1.308], dtype=np.float32)


def _estimate_frame(kp: np.ndarray) -> np.ndarray:
    """Wrist-rooted orientation frame from wrist(0), index_mcp(5), middle_mcp(9).

    Makes the retargeting invariant to the hand's global orientation in the image.
    Mirrors dex-retargeting's SingleHandDetector.estimate_frame_from_hand_points.
    """
    points = kp[[0, 5, 9], :]
    x_vec = points[0] - points[2]                 # wrist - middle_mcp
    pc = points - points.mean(axis=0, keepdims=True)
    _, _, v = np.linalg.svd(pc)
    normal = v[2, :]
    x = x_vec - np.sum(x_vec * normal) * normal
    x = x / (np.linalg.norm(x) + 1e-9)
    z = np.cross(x, normal)
    if np.sum(z * (pc[1] - pc[2])) < 0:
        normal = -normal
        z = -z
    return np.stack([x, normal, z], axis=1)


class DexRetarget:
    """dex-retargeting wrapper for one hand chirality."""

    def __init__(self, hand_type: str = "right", urdf_dir: str | None = None,
                 low_pass_alpha: float = 1.0, use_dexpilot: bool = False):
        from dex_retargeting.retargeting_config import RetargetingConfig
        from dex_retargeting.constants import (
            RobotName, RetargetingType, HandType, get_default_config_path,
        )

        urdf_dir = urdf_dir or DEFAULT_URDF_DIR
        if not os.path.isdir(urdf_dir):
            raise FileNotFoundError(f"dex-urdf assets not found: {urdf_dir}")
        RetargetingConfig.set_default_urdf_dir(urdf_dir)

        self.hand_type = hand_type
        self._op2mano = OPERATOR2MANO_RIGHT if hand_type == "right" else OPERATOR2MANO_LEFT
        ht = HandType.right if hand_type == "right" else HandType.left
        # vector = independent fingertip matching (no inter-finger coupling, faster).
        # dexpilot = pinch-optimized but lifts other fingers near a pinch + slower.
        rtype = RetargetingType.dexpilot if use_dexpilot else RetargetingType.vector
        cfg_path = get_default_config_path(RobotName.inspire, rtype, ht)
        cfg = RetargetingConfig.load_from_file(cfg_path)
        # Bundled config low_pass_alpha=0.2 is heavy smoothing (laggy);
        # 1.0 = no filter = no added latency.
        try:
            cfg.low_pass_alpha = float(low_pass_alpha)
        except Exception:
            pass
        self.ret = cfg.build()

        full = list(self.ret.joint_names)
        tgt  = list(self.ret.optimizer.target_joint_names)   # our DOF order
        self._idx = [full.index(n) for n in tgt]
        self._human_indices = np.asarray(self.ret.optimizer.target_link_human_indices)

        # Per-finger open reference (rad). A relaxed open human hand makes the
        # optimizer settle outer fingers slightly flexed (q>0), so they never
        # reach 1000. set_open_q() captures those and stretches them to 1000.
        self.open_q = np.zeros(6, dtype=np.float32)
        self.last_q6 = np.zeros(6, dtype=np.float32)

    def set_open_q(self, q6, scale: float = 1.0) -> None:
        """Set open reference for PINKY (0) and RING (1) only.

        Index/middle keep their default mapping (q=0 -> 1000) so the pinch is
        unaffected. ``scale`` < 1 applies a partial boost ("open a bit more").
        """
        q = np.asarray(q6, dtype=np.float32)
        for i in (0, 1):   # pinky, ring
            self.open_q[i] = float(np.clip(q[i] * scale, 0.0, _UPPER[i] - 0.05))

    def retarget(self, world_landmarks: np.ndarray) -> list[int]:
        """world_landmarks: (21, 3) MediaPipe metric landmarks. Returns 6 ints."""
        kp = np.asarray(world_landmarks, dtype=np.float32)
        frame = _estimate_frame(kp)
        joint_pos = kp @ frame @ self._op2mano

        origin = self._human_indices[0]
        task   = self._human_indices[1]
        ref = joint_pos[task, :] - joint_pos[origin, :]

        qpos = self.ret.retarget(ref)
        q6 = np.array([qpos[i] for i in self._idx], dtype=np.float32)
        self.last_q6 = q6
        # rad -> Inspire, stretched so open_q (relaxed open) -> 1000, upper -> 0
        denom = np.maximum(_UPPER - self.open_q, 1e-3)
        inspire = np.clip(1000.0 * (_UPPER - q6) / denom, 0, 1000)
        return [int(round(v)) for v in inspire]
