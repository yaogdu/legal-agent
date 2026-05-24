#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PROFILE="${1:-default}"
echo "=== restarting services ==="
if [[ "$PROFILE" == "full" ]]; then
  bash scripts/stop.sh all
else
  bash scripts/stop.sh core
fi
bash scripts/start.sh "$PROFILE"
