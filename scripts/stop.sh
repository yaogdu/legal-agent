#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${1:-all}"

if [[ "$PROFILE" == "core" ]]; then
  echo "=== stopping legal-agent containers ==="
  docker compose stop legal-agent-api legal-agent-worker rag-worker embedding-worker
else
  echo "=== stopping all services ==="
  COMPOSE_PROFILES=observability docker compose stop
fi
