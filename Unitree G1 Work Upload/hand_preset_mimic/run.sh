#!/usr/bin/env bash
# hand_preset_mimic launcher.
# Usage:
#   bash run.sh              # gesture -> preset playback (camera mode)
#   bash run.sh recorder     # animation recorder (blue theme)
#   bash run.sh recorder-bw  # animation recorder (black & white theme)
#   bash run.sh --dry-run    # camera mode, no hardware
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEXKIT="$(cd "$HERE/../../../300-software/60-libs/dexkit" && pwd)"

cd "$HERE"
export PYTHONPATH="$DEXKIT:$HERE/src"

CMD="${1:-}"
if [[ "$CMD" == "recorder" ]]; then
    shift
    uv run python src/recorder.py --theme blue "$@"
elif [[ "$CMD" == "recorder-bw" ]]; then
    shift
    uv run python src/recorder.py --theme bw "$@"
else
    uv run python src/mimic.py \
        --source 8 --width 1920 --height 1080 \
        --mirror --gpu \
        --config config/hand_preset_mimic.yaml \
        "$@"
fi
