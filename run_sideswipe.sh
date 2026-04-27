#!/usr/bin/env bash
# run_sideswipe.sh — activate .venv and launch the hand-tracking agent.
# Extra args are forwarded to agent.py (e.g. --headless, --quiet).

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

if [[ ! -d "$REPO_DIR/.venv" ]]; then
  echo "❌ No .venv found. Run:  bash setup_mac.sh" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$REPO_DIR/.venv/bin/activate"
exec python "$REPO_DIR/src/agent.py" "$@"
