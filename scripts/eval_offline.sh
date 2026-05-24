#!/usr/bin/env bash
set -euo pipefail

DATASET="${DATASET:-evaluations/labor_dispute/offline_minimal.json}"
OUT="${OUT:-evaluation-reports/offline-minimal.json}"
FAIL_ON_GATE="${FAIL_ON_GATE:-1}"
LOCAL="${LOCAL:-0}"

args=(eval-offline --dataset "$DATASET")
if [[ "$FAIL_ON_GATE" == "0" ]]; then
  args+=(--no-fail-on-gate)
fi

mkdir -p "$(dirname "$OUT")"
if [[ "$LOCAL" == "1" ]]; then
  legal-agent "${args[@]}" --out "$OUT"
else
  docker compose exec -T legal-agent-api legal-agent "${args[@]}" | tee "$OUT" >/dev/null
fi

python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(json.dumps({"dataset_id":data["dataset_id"],"passed":data["passed"],"case_count":data["case_count"],"metrics":data["metrics"],"gates":data["gates"]}, ensure_ascii=False, indent=2, sort_keys=True))' "$OUT"
