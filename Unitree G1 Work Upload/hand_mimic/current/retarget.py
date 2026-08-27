#!/usr/bin/env python3
"""Retarget MediaPipe hand landmarks to Inspire RH56 joint angles.

Output is 6 integers in ``JOINT_NAMES`` order
``[pinky, ring, middle, index, thumb_bend, thumb_rot]`` on the Inspire scale
``0`` = bent/closed, ``1000`` = open.

Thumb handling has two layers:
  1. **Pinch detection** — when the thumb tip is close to the index or middle
     tip, skip geometry entirely and snap to a hardware preset. The RH56 thumb
     cannot pinch ring/pinky, so those fingers are never treated as pinch targets.
  2. **Curl (bend)** — uses normalized distance from the thumb tip to the palm
     centre. Much more stable than an IP-joint angle when the thumb folds across
     the palm, and decoupled from the tilt/abduction axis.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields

import numpy as np

# Per-finger landmark triplets (mcp, pip, tip) for the curl angle.
_FINGER_TRIPLETS = {
    "index":  (5,  6,  8),
    "middle": (9,  10, 12),
    "ring":   (13, 14, 16),
    "pinky":  (17, 18, 20),
}

ANGLE_MIN = 0
ANGLE_MAX = 1000


@dataclass
class Calibration:
    # Four-finger curl bounds (degrees, open → closed)
    curl_open_deg: float = 170.0
    curl_closed_deg: float = 50.0

    # Thumb curl: normalised tip-to-palm-centre distance.
    # Large distance (thumb extended) → 1000 (open); small → 0 (closed).
    # Diagnostic showed fist gives ~0.39, open gives ~1.36.
    thumb_curl_far: float = 1.0    # fully extended (clamp above this → 1000)
    thumb_curl_close: float = 0.50 # fully curled in fist (at or below → 0)

    # Thumb rotation/spread: normalised x-offset of thumb tip from index MCP.
    # (thumb_tip.x - index_mcp.x) / palm_width
    # Diagnostic: fist = -0.71 (thumb crosses palm), open = +0.85 (thumb spread).
    # Tucked (low x-offset) → rot 0;  Spread (high x-offset) → rot 1000.
    thumb_rot_x_tucked: float = -0.8   # fully tucked/fist  → 0
    thumb_rot_x_spread: float = 1.0    # fully spread/open  → 1000

    # Pinch detection: normalised tip-to-tip distance (relative to palm width).
    pinch_threshold: float = 0.28

    # Preset (bend, rot) sent to the Inspire hand when a pinch is detected.
    # RH56 thumb can only reach index and middle — no ring/pinky presets.
    pinch_index_bend: int = 0
    pinch_index_rot: int = 833    # inverted: Inspire rot=0 is spread, 1000 is tucked
    pinch_middle_bend: int = 0
    pinch_middle_rot: int = 650
    pinch_both_bend: int = 0
    pinch_both_rot: int = 750

    per_finger: dict[str, tuple[float, float]] = field(default_factory=dict)

    # Startup poses as raw POS_ACT encoder values (0=open, 2000=bent).
    # set_positions() is used — exact actuator copy, no firmware conversion.
    startup_right_pos: list[int] = field(default_factory=list)
    startup_left_pos:  list[int] = field(default_factory=list)

    # Per-DOF EMA smoothing learned from the index-pinch calibration, so the
    # thumb keeps pace with the index during a pinch. 6 values, order JOINT_NAMES.
    pinch_alpha: list[float] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "Calibration":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


DEFAULT_CALIB = Calibration()


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _angle_deg(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Interior angle at vertex b (degrees)."""
    v1, v2 = a - b, c - b
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 180.0
    return float(np.degrees(np.arccos(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))))


def _scale(value: float, lo: float, hi: float, invert: bool = False) -> int:
    """Map value ∈ [lo, hi] onto the Inspire 0–1000 scale (clamped)."""
    frac = 0.0 if abs(hi - lo) < 1e-6 else (value - lo) / (hi - lo)
    frac = min(1.0, max(0.0, frac))
    if invert:
        frac = 1.0 - frac
    return int(round(ANGLE_MIN + frac * (ANGLE_MAX - ANGLE_MIN)))


def _palm_size(lm: np.ndarray) -> float:
    """Index MCP (5) → pinky MCP (17) distance — robust palm-width proxy."""
    return float(np.linalg.norm(lm[5] - lm[17])) + 1e-6


# --- Raw metric functions (shared by live mapping + calibration tool) ---

def finger_curl_deg(lm: np.ndarray, name: str) -> float:
    """Joint angle (deg) for a finger. ~170 extended, ~50 curled."""
    mcp, pip, tip = _FINGER_TRIPLETS[name]
    return _angle_deg(lm[mcp], lm[pip], lm[tip])


def thumb_curl_metric(lm: np.ndarray) -> float:
    """Thumb bend measured as the interior angle at the IP joint (lm[3]).

    Landmarks: CMC=1, MCP=2, IP=3, tip=4.
    The IP-joint angle captures thumb flexion directly and works even when
    the thumb moves toward the index (OK pinch) rather than the palm,
    where the tip-to-palm-centre distance barely changes.
    ~170 deg = thumb straight (open), ~80 deg = thumb curled (bend).
    """
    lm = np.asarray(lm, dtype=np.float32)
    return _angle_deg(lm[2], lm[3], lm[4])   # angle at IP joint


def thumb_rot_metric(lm: np.ndarray, left: bool = False) -> float:
    """Normalised x-offset of thumb tip from index MCP (right-hand frame)."""
    lm = np.asarray(lm, dtype=np.float32)
    x = float(lm[4][0] - lm[5][0]) / _palm_size(lm)
    return -x if left else x


def _detect_pinch(lm: np.ndarray, palm: float, threshold: float) -> str | None:
    """Return 'index', 'middle', 'both', or None.

    Measures thumb tip (4) distance to index tip (8) and middle tip (12).
    Also requires the target finger to be at least partially closed (its tip
    below the knuckle in y) so we don't false-trigger when fingers pass nearby.
    """
    d_idx = float(np.linalg.norm(lm[4] - lm[8]))  / palm
    d_mid = float(np.linalg.norm(lm[4] - lm[12])) / palm

    # Finger must not be fully extended — tip closer to palm centre than
    # when straight. Avoids false trigger when thumb passes near an open finger.
    palm_c = (lm[5] + lm[17]) / 2.0
    idx_not_extended = float(np.linalg.norm(lm[8]  - palm_c)) / palm < 1.1
    mid_not_extended = float(np.linalg.norm(lm[12] - palm_c)) / palm < 1.1

    pi = d_idx < threshold and idx_not_extended
    pm = d_mid < threshold and mid_not_extended

    if pi and pm:  return "both"
    if pi:         return "index"
    if pm:         return "middle"
    return None


def _thumb_curl(lm: np.ndarray, calib: Calibration) -> int:
    """Normalised thumb tip → palm-centre distance → Inspire scale.

    Decoupled from tilt: when the thumb folds toward the palm the tip
    approaches the palm centre regardless of whether it also tilts.
    """
    dist = thumb_curl_metric(lm)
    return _scale(dist, calib.thumb_curl_close, calib.thumb_curl_far)


def _thumb_tilt(lm: np.ndarray, calib: Calibration, left: bool = False) -> int:
    """Thumb rotation/spread: normalised x-offset of tip from index MCP.

    (thumb_tip.x - index_mcp.x) / palm_width.
    For a right hand with --mirror: negative = tucked/fist → 0,
    positive = spread → 1000.
    For a left hand the x direction is mirrored, so we negate the offset.
    """
    x_offset = thumb_rot_metric(lm, left=left)
    # No invert: spread (pose A, thumb to side) -> 1000, tucked (pose B,
    # aligned with middle) -> 0, matching the Inspire thumb_rot direction here.
    return _scale(x_offset, calib.thumb_rot_x_tucked, calib.thumb_rot_x_spread)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def landmarks_to_angles(
    landmarks: np.ndarray,
    calib: Calibration = DEFAULT_CALIB,
    hand_side: str = "right",
) -> tuple[list[int], str | None]:
    """Convert one hand's (21, 3) landmark array to 6 Inspire joint angles.

    Args:
        landmarks: (21, 3) array from MediaPipe (image-space, already mirrored
                   if ``--mirror`` was used before tracking).
        calib: calibration parameters.
        hand_side: ``"left"`` or ``"right"`` (physical hand). The x-offset
                   signal is negated for the left hand because after a mirror
                   flip the left thumb spreads in the opposite x-direction.

    Returns:
        ``(angles, pinch_mode)`` where ``angles`` is
        ``[pinky, ring, middle, index, thumb_bend, thumb_rot]`` (0-1000) and
        ``pinch_mode`` is ``"index"``, ``"middle"``, ``"both"``, or ``None``.
        Callers should snap (not smooth) the thumb channels when pinch_mode
        is not None.
    """
    lm = np.asarray(landmarks, dtype=np.float32)
    if lm.shape[0] < 21:
        raise ValueError(f"expected 21 landmarks, got {lm.shape[0]}")

    palm = _palm_size(lm)

    palm_c = (lm[5] + lm[17]) / 2.0

    def curl(name: str) -> int:
        mcp, pip, tip = _FINGER_TRIPLETS[name]
        deg = _angle_deg(lm[mcp], lm[pip], lm[tip])
        lo, hi = calib.per_finger.get(
            name, (calib.curl_closed_deg, calib.curl_open_deg))
        v = _scale(deg, lo, hi)
        # Occlusion guard: when the fingertip is physically close to the palm
        # (tucked fist), the tip landmark gets occluded and the angle jumps to
        # ~180° (reads as open). If the tip is within 0.6 palm-widths of the
        # palm centre, cap the output at its closed end regardless of the angle.
        tip_dist = float(np.linalg.norm(lm[tip] - palm_c)) / palm
        if tip_dist < 0.6:
            v = min(v, 150)   # clamp: close but allow some slack for ring/pinky
        return v

    pinky  = curl("pinky")
    ring   = curl("ring")
    middle = curl("middle")
    index  = curl("index")

    # --- Thumb: pinch preset takes priority over geometry ---
    pinch = _detect_pinch(lm, palm, calib.pinch_threshold)
    if pinch == "index":
        thumb_bend = calib.pinch_index_bend
        thumb_rot  = calib.pinch_index_rot
    elif pinch == "middle":
        thumb_bend = calib.pinch_middle_bend
        thumb_rot  = calib.pinch_middle_rot
    elif pinch == "both":
        thumb_bend = calib.pinch_both_bend
        thumb_rot  = calib.pinch_both_rot
    else:
        thumb_bend = 1000 - _thumb_curl(lm, calib)   # flip: hardware bend is inverted
        thumb_rot  = _thumb_tilt(lm, calib, left=(hand_side == "left"))

    return [pinky, ring, middle, index, thumb_bend, thumb_rot], pinch


# ---------------------------------------------------------------------------
# EMA smoother
# ---------------------------------------------------------------------------

class AngleSmoother:
    """Exponential moving average over 6-DOF angle vectors.

    Accepts a scalar (all channels) or a length-6 sequence for per-channel
    smoothing (e.g. smooth the jittery thumb harder than the fingers).
    """

    def __init__(self, alpha: float | list[float] = 0.4) -> None:
        self.alpha = np.asarray(alpha, dtype=np.float32)
        if np.any(self.alpha <= 0.0) or np.any(self.alpha > 1.0):
            raise ValueError("alpha must be in (0, 1]")
        self._state: np.ndarray | None = None

    def update(self, angles: list[int]) -> list[int]:
        a = np.asarray(angles, dtype=np.float32)
        if self._state is None:
            self._state = a
        else:
            self._state = self.alpha * a + (1.0 - self.alpha) * self._state
        return [int(round(v)) for v in self._state]

    def reset(self) -> None:
        self._state = None
