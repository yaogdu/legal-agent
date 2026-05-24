#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== service status ==="
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

echo ""
echo "=== health check ==="
if docker compose exec -T legal-agent-api curl -s http://localhost:8080/health 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
checks = data.get('checks', [])
ok = sum(1 for c in checks if c['status'] == 'ok')
failed = sum(1 for c in checks if c['status'] == 'failed')
skipped = sum(1 for c in checks if c['status'] == 'skipped')
print(f\"health: {ok} ok, {failed} failed, {skipped} skipped\")
for c in checks:
    print(f\"  {c['name']}: {c['status']}\")
"; then
  :
else
  echo "health: api not reachable"
fi
