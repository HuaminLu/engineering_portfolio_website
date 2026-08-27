#!/usr/bin/env python3
"""policy_demo.py — headless test for the continuous arm policy.

Loads the ``arm_policy.joblib`` bundle built by ``build_preset_policy.py`` and
prints the target pose for a blended command, or sweeps one axis to show smooth
magnitude interpolation. No hardware / DDS needed.

Usage
=====
    # single blended command "70% forward + 30% up"
    python3 data/policy_demo.py --arm left --cmd 0.7,0,0.3

    # from a non-home current pose
    python3 data/policy_demo.py --arm left --cmd 1,0,0 --start 0,0,0,0,0,0,0

    # sweep magnitude 0 -> 1 along one axis (shows interpolation between presets)
    python3 data/policy_demo.py --arm left --sweep forward

Command axes: (forward/back, right/left, up/down), each ~[-1, 1].
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# Allow running as `python3 data/policy_demo.py` from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.inference_arm import (  # noqa: E402
    load_policy,
    predict_policy_target,
    POLICY_AXIS_MAP,
)


def _fmt(vec) -> str:
    return "[" + ", ".join(f"{v:+.3f}" for v in vec) + "]"


def main() -> None:
    ap = argparse.ArgumentParser(description="Demo the continuous arm policy")
    ap.add_argument("--arm", choices=["left", "right"], default="left")
    ap.add_argument("--cmd", default=None,
                    help="command as 'fb,lr,ud' e.g. 0.7,0,0.3")
    ap.add_argument("--start", default=None,
                    help="current pose as 7 comma-separated radians (default = home)")
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--sweep", default=None, choices=list(POLICY_AXIS_MAP.keys()),
                    help="sweep magnitude 0..1 along one named direction")
    ap.add_argument("--steps", type=int, default=6, help="sweep steps (default 6)")
    args = ap.parse_args()

    bundle = load_policy(arm=args.arm)
    home = np.asarray(bundle.get("home", [0.0] * 7), dtype=float)
    start = (np.asarray([float(x) for x in args.start.split(",")], dtype=float)
             if args.start else home.copy())

    print(f"[demo] arm={args.arm}  trained dirs={bundle.get('directions')}")
    print(f"[demo] axis order = (forward/back, right/left, up/down)")
    print(f"[demo] start pose = {_fmt(start)}")
    print()

    if args.sweep:
        axis = np.asarray(bundle["axis_map"][args.sweep], dtype=float)
        print(f"[demo] sweeping '{args.sweep}' magnitude 0 -> 1 ({args.steps} steps):")
        for i in range(args.steps + 1):
            t = i / args.steps
            cmd = (t * axis).tolist()
            tgt = predict_policy_target(cmd, start, arm=args.arm,
                                        bundle=bundle, gain=args.gain)
            print(f"   t={t:.2f}  cmd={_fmt(cmd)}  ->  {_fmt(tgt)}")
        return

    if not args.cmd:
        ap.error("pass --cmd fb,lr,ud  (or --sweep <direction>)")
    cmd = [float(x) for x in args.cmd.split(",")]
    tgt = predict_policy_target(cmd, start, arm=args.arm, bundle=bundle, gain=args.gain)
    print(f"[demo] command = {_fmt(cmd)}")
    print(f"[demo] target  = {_fmt(tgt)}")
    print(f"[demo] delta   = {_fmt(np.asarray(tgt) - start)}")


if __name__ == "__main__":
    main()
