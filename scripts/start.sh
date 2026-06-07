#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PROFILE="${1:-default}"
WORKERS="${LEGAL_AGENT_WORKERS:-2}"
RAG_WORKERS="${LEGAL_AGENT_RAG_WORKERS:-2}"
EMBEDDING_WORKERS="${LEGAL_AGENT_EMBEDDING_WORKERS:-2}"

if [[ "$PROFILE" == "full" ]]; then
  export COMPOSE_PROFILES=observability
  echo "=== starting all services (legal-agent + langfuse) ==="
else
  echo "=== starting core services (legal-agent only) ==="
  echo "    use 'scripts/start.sh full' to include langfuse"
fi

docker compose up -d postgres temporal
docker compose exec -T postgres pg_isready -U legal_agent -d legal_agent -t 30 >/dev/null 2>&1

for i in $(seq 1 20); do
  if docker compose exec -T temporal temporal operator cluster health 2>/dev/null | grep -q SERVING; then
    break
  fi
  sleep 2
done

docker compose up -d temporal-ui legal-agent-api

if [[ "$PROFILE" == "full" ]]; then
  docker compose up -d redis minio clickhouse
  for i in $(seq 1 30); do
    if docker inspect legal-agent-clickhouse-1 --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null | grep -q healthy; then
      break
    fi
    sleep 2
  done
  docker compose up -d langfuse-db-init
  docker compose up -d langfuse-web
  for i in $(seq 1 30); do
    if docker inspect legal-agent-langfuse-web-1 --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' 2>/dev/null | grep -q healthy; then
      break
    fi
    sleep 2
  done
  docker compose up -d langfuse-worker
fi

docker compose up -d --scale legal-agent-worker="$WORKERS" --scale rag-worker="$RAG_WORKERS" --scale embedding-worker="$EMBEDDING_WORKERS" legal-agent-worker rag-worker embedding-worker

echo ""
echo "api:          http://localhost:28080"
echo "temporal ui:  http://localhost:28088"
echo "workers:      legal-agent=${WORKERS} rag=${RAG_WORKERS} embedding=${EMBEDDING_WORKERS}"
if [[ "$PROFILE" == "full" ]]; then
  echo "langfuse:     http://localhost:3001"
fi
