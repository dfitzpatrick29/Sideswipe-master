#!/usr/bin/env bash
# setup_mac.sh — one-shot installer for Sideswipe on macOS.
#
# Usage:   bash setup_mac.sh
# Effects: creates .venv, installs deps, pre-downloads the MediaPipe model.
# Idempotent: safe to re-run.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

echo "📍 Repo:   $REPO_DIR"

# ── 1. Pick a Python whose version MediaPipe publishes wheels for.
#       Preference order: 3.12 → 3.11 → 3.10 → 3.13 → 3.9.
#       Also checks common Homebrew install paths directly, in case the
#       versioned binary isn't symlinked onto PATH.
pick_python() {
  local candidates=(
    python3.12 python3.11 python3.10 python3.13 python3.9 python3
    /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11
    /opt/homebrew/bin/python3.10 /opt/homebrew/bin/python3.13
    /usr/local/bin/python3.12   /usr/local/bin/python3.11
    /usr/local/bin/python3.10   /usr/local/bin/python3.13
  )
  local c ver
  for c in "${candidates[@]}"; do
    if [[ -x "$c" ]] || command -v "$c" >/dev/null 2>&1; then
      ver="$("$c" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
      case "$ver" in
        3.9|3.10|3.11|3.12|3.13) command -v "$c" || echo "$c"; return 0 ;;
      esac
    fi
  done
  return 1
}

PY="$(pick_python || true)"

if [[ -z "${PY:-}" ]]; then
  echo "🔎 No compatible Python found on PATH. Checking Homebrew…"
  if command -v brew >/dev/null 2>&1; then
    echo "🍺 Installing python@3.12 via Homebrew (this may take a minute)…"
    brew install python@3.12
    # Homebrew doesn't always symlink onto PATH; try direct paths.
    PY="$(pick_python || true)"
  else
    cat <<'EOF' >&2
❌ Could not find Python 3.9–3.13 on your PATH, and Homebrew isn't installed.

Install Homebrew (recommended) with:
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

Then:
    brew install python@3.12
    bash setup_mac.sh
EOF
    exit 1
  fi
fi

if [[ -z "${PY:-}" ]]; then
  echo "❌ Still no compatible Python after attempting brew install." >&2
  echo "   Try manually:  brew install python@3.12  && bash setup_mac.sh" >&2
  exit 1
fi

echo "🐍 Python: $("$PY" --version) at $(command -v "$PY" || echo "$PY")"

# ── 2. Create / reuse a local virtualenv ──
VENV_DIR="$REPO_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
  echo "🧪 Creating virtualenv at .venv"
  "$PY" -m venv "$VENV_DIR"
else
  echo "🧪 Reusing existing virtualenv at .venv"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── 3. Install deps ──
echo "⬆️  Upgrading pip / wheel"
python -m pip install --upgrade pip wheel >/dev/null

echo "📦 Installing requirements.txt"
python -m pip install -r requirements.txt

# ── 4. Pre-download the MediaPipe hand model ──
MODEL="$REPO_DIR/hand_landmarker.task"
if [[ ! -f "$MODEL" ]]; then
  echo "⬇️  Downloading MediaPipe hand model"
  curl -fSL -o "$MODEL" \
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
fi
echo "✓ Model: $MODEL"

cat <<EOF

✅ Setup complete.

Run the app:
    bash run_sideswipe.sh

First launch: macOS will prompt to grant Camera permission to
"Terminal" (or iTerm). Approve it, then re-run. If nothing happens,
also grant Accessibility access in:
  System Settings → Privacy & Security → Accessibility

Controls:
  SPACE      toggle active / paused
  👏👏      (two-hand clap) also toggles active
  q          quit

EOF
