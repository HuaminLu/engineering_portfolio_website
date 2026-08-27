"""Shared config loading for hand_mimic scripts."""

from __future__ import annotations

import logging
from pathlib import Path

from dexkit.mapping import Calibration, DEFAULT_CALIB


def load_calibration(path: Path | None, log: logging.Logger) -> Calibration:
    """Load the 'calibration:' section from a YAML file, or return defaults."""
    if path is None:
        return DEFAULT_CALIB
    if not path.exists():
        log.error("config not found: %s — using default calibration", path)
        return DEFAULT_CALIB
    import yaml

    data = yaml.safe_load(path.read_text()) or {}
    calib = Calibration.from_dict(data.get("calibration", {}))
    log.info("loaded calibration from %s", path)
    return calib
