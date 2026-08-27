#!/usr/bin/env bash
# hand_mimic setup for a standalone Jetson Orin (JetPack 6 / system python3).
#
# Installs deps from the jetson-ai-lab wheel index, downloads the MediaPipe
# hand model, and editable-installs the shared dexkit library.
#
# Per repo policy, the Jetson uses the SYSTEM python3 — never uv/conda/pyenv.
set -euo pipefail

JP_INDEX="https://pypi.jetson-ai-lab.io/jp6"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEXKIT="$(cd "$HERE/../../../300-software/60-libs/dexkit" && pwd)"
MODEL_DIR="$HERE/config"
MODEL_URL="https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"

if command -v uv >/dev/null 2>&1 && [[ "${ALLOW_UV:-0}" != "1" ]]; then
  echo "Refusing to use uv on the Jetson (system python3 only)." >&2
  echo "If you are on the x86 workstation, run: uv pip install -r requirements.txt" >&2
fi

echo "==> Installing Python deps (system python3, jetson-ai-lab index)"
# opencv is system-provided on JetPack; do not pip-install opencv-python here.
pip3 install --extra-index-url "$JP_INDEX" numpy'<2' pyserial pyyaml
pip3 install --extra-index-url "$JP_INDEX" mediapipe

echo "==> Editable-installing dexkit from $DEXKIT"
pip3 install --extra-index-url "$JP_INDEX" -e "$DEXKIT"

echo "==> Downloading MediaPipe hand model -> $MODEL_DIR/hand_landmarker.task"
mkdir -p "$MODEL_DIR"
if [[ -f "$MODEL_DIR/hand_landmarker.task" ]]; then
  echo "    already present, skipping"
else
  curl -fSL "$MODEL_URL" -o "$MODEL_DIR/hand_landmarker.task"
fi

echo "==> Done. Verify camera:  python3 src/camera.py --probe"
echo "    Recognition view:     python3 src/recognize.py"
echo "    End-to-end mimic:     python3 src/mimic.py"
