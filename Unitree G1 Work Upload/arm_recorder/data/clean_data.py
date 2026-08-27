#!/usr/bin/env python3
"""clean_data.py — normalise the arm training CSV to an aligned, arm-only layout.

The recorder originally wrote a waist column (motor index 12).  After the waist
was dropped, new sessions started appending 17-column arm-only rows *under the
old 19-column header* — so the file mixes two row widths and pandas (which reads
by column *name*) misaligns every arm-only row.

This script rewrites the CSV in place (keeping a ``.bak`` backup) so that:

* the header is the arm-only 17-column form
  ``direction,session_id,quality,start_15..21,end_15..21``
* every kept data row has exactly those 7 start + 7 end arm joints
* legacy 19-column rows (with the waist) are converted by dropping the waist
  columns, or skipped if malformed
* the recorder's ``# session ...`` bracket comments are preserved

Idempotent: running it again on an already-clean file is a no-op (besides the
backup refresh).

    python3 data/clean_data.py --arm left
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

# Arm joint motor indices (waist index 12 is intentionally excluded).
LEFT_IDX = [15, 16, 17, 18, 19, 20, 21]
RIGHT_IDX = [22, 23, 24, 25, 26, 27, 28]
DIRECTIONS = {"forward", "back", "left", "right", "up", "down"}

META_COLS = ["direction", "session_id", "quality"]


def _arm_idx(arm: str) -> list[int]:
    return LEFT_IDX if arm == "left" else RIGHT_IDX


def _header(arm: str) -> str:
    idx = _arm_idx(arm)
    cols = META_COLS + [f"start_{i}" for i in idx] + [f"end_{i}" for i in idx]
    return ",".join(cols)


def _default_csv(arm: str) -> Path:
    return Path("data") / "arms" / arm / "training_data_with_waist.csv"


def clean(csv_path: Path, arm: str) -> None:
    if not csv_path.exists():
        raise SystemExit(f"No CSV at {csv_path}")

    raw = csv_path.read_text().splitlines()

    want_start = 3 + len(_arm_idx(arm))          # 3 meta + 7 start = 10
    want_total = 3 + 2 * len(_arm_idx(arm))       # + 7 end        = 17
    legacy_total = want_total + 2                  # waist adds start_12 + end_12 = 19

    kept_rows: list[str] = []
    n_data = n_converted = n_dropped = n_comments = 0

    for line in raw:
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            kept_rows.append(s)      # preserve session bracket comments
            n_comments += 1
            continue

        fields = s.split(",")

        # skip any header row (old or new) — we re-emit our own
        if fields[0] == "direction":
            continue

        # only keep real direction rows
        if fields[0] not in DIRECTIONS:
            n_dropped += 1
            continue

        if len(fields) == want_total:
            kept_rows.append(",".join(fields))
            n_data += 1
        elif len(fields) == legacy_total:
            # legacy waist row: fields are meta(3) + start_12 + start_arm(7)
            #                                      + end_12 + end_arm(7)
            meta = fields[0:3]
            start_arm = fields[4:4 + len(_arm_idx(arm))]      # drop start_12 at idx 3
            end_off = 3 + 1 + len(_arm_idx(arm))              # after meta+start_12+start_arm
            end_arm = fields[end_off + 1:end_off + 1 + len(_arm_idx(arm))]  # drop end_12
            new = meta + start_arm + end_arm
            if len(new) == want_total:
                kept_rows.append(",".join(new))
                n_converted += 1
            else:
                n_dropped += 1
        else:
            n_dropped += 1

    # backup then rewrite
    backup = csv_path.with_suffix(csv_path.suffix + ".bak")
    shutil.copy2(csv_path, backup)

    out_lines = [_header(arm)] + kept_rows
    csv_path.write_text("\n".join(out_lines) + "\n")

    total = n_data + n_converted
    print(f"[clean] arm={arm}  file={csv_path}")
    print(f"[clean] backup written to {backup}")
    print(f"[clean] kept {total} samples "
          f"({n_data} already arm-only, {n_converted} converted from waist rows)")
    print(f"[clean] dropped {n_dropped} malformed/unknown rows, "
          f"preserved {n_comments} comment lines")
    print(f"[clean] header -> {_header(arm)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Clean/align the arm training CSV")
    ap.add_argument("--arm", choices=["left", "right"], default="left")
    ap.add_argument("--csv", default=None, help="override CSV path")
    args = ap.parse_args()
    csv_path = Path(args.csv) if args.csv else _default_csv(args.arm)
    clean(csv_path, args.arm)


if __name__ == "__main__":
    main()
