#!/usr/bin/env bash
# Deploys current/ instance and runs it.
# This is the canonical "run" command — always start from here.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd "$HERE/.." && pwd)"
DEXKIT="$(cd "$PROJ/../../../300-software/60-libs/dexkit" && pwd)"

cp "$HERE/mimic.py"       "$PROJ/src/mimic.py"
cp "$HERE/dex_backend.py" "$DEXKIT/dexkit/mapping/dex_backend.py"
cp "$HERE/retarget.py"    "$DEXKIT/dexkit/mapping/retarget.py"

cd "$PROJ"
uv run python src/mimic.py \
    --source 8 --width 1920 --height 1080 \
    --mirror --gpu --swap-hands \
    --backend hybrid \
    --config current/hand_mimic.yaml \
    --pinch-rot-offset 10 \
    --fps 0 \
    --deadband 2 \
    "$@"
