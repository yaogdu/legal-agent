#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:28080}"
idempotency_prefix="smoke-$(date +%s)-$RANDOM"

wait_for_run_status() {
  local target_run_id="$1"
  local expected="$2"
  local response=""
  local status=""
  for _ in $(seq 1 30); do
    response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$target_run_id")"
    status="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_status"])' <<<"$response")"
    if [[ "$status" == "$expected" ]]; then
      echo "$response"
      return 0
    fi
    sleep 1
  done
  echo "$response"
  echo "expected status $expected, got $status" >&2
  return 1
}

health_details_ok() {
  python3 -c 'import json,sys; data=json.load(sys.stdin); checks={c.get("name"): c for c in data.get("checks", [])}; required=["database","temporal_server","temporal_worker","temporal_search_attributes","rag_worker","embedding_worker","rag","agentledger","shared_volume"]; failed=[name for name in required if checks.get(name, {}).get("status") != "ok"]; assert data.get("status") == "ok", data; assert not failed, failed; assert checks.get("llm", {}).get("status") in {"ok","skipped"}; assert checks.get("langfuse", {}).get("status") in {"ok","skipped"}' <<<"$1"
}

wait_for_health_details() {
  local response=""
  for _ in $(seq 1 30); do
    response="$(curl -fsS "$BASE_URL/healthz/details" || true)"
    if [[ -n "$response" ]] && health_details_ok "$response" >/dev/null 2>&1; then
      echo "$response"
      return 0
    fi
    sleep 1
  done
  echo "$response"
  health_details_ok "$response"
}

echo "health:"
curl -fsS "$BASE_URL/healthz"
echo

echo "health details:"
health_details_response="$(wait_for_health_details)"
echo "$health_details_response"
echo "health details ok"
echo

echo "metrics:"
metrics_response="$(curl -fsS "$BASE_URL/metrics")"
python3 -c 'import sys; data=sys.stdin.read(); assert "legal_agent_info" in data; assert "agentledger_tables_ready" in data; assert "rag_source_documents_total" in data' <<<"$metrics_response"
echo "metrics ok"
echo

material_file="$(mktemp -t legal-agent-material.XXXXXX.md)"
docx_file="$(mktemp -t legal-agent-docx.XXXXXX)"
trap 'rm -f "$material_file" "$docx_file"' EXIT
printf '%s\n' \
  '# 劳动争议证据材料' \
  '申请人张三的工资流水显示月工资为 15000 元。' \
  '聊天记录显示北京某科技有限公司拖欠 2 个月工资，并在未提前通知的情况下解除劳动关系。' \
  '申请人保存了考勤记录、工牌照片和解除通知截图。' >"$material_file"

echo "upload file:"
upload_key="${idempotency_prefix}-upload"
upload_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/files" -H "Idempotency-Key: $upload_key" -F "file=@${material_file};filename=smoke_evidence.md;type=text/markdown")"
echo "$upload_response"
file_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["file_id"])' <<<"$upload_response")"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["parse_status"] == "PARSED"; assert data["chunk_count"] > 0' <<<"$upload_response"
upload_replay_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/files" -H "Idempotency-Key: $upload_key" -F "file=@${material_file};filename=smoke_evidence.md;type=text/markdown")"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["file_id"] == first["file_id"]; assert second["sha256"] == first["sha256"]' "$upload_response" <<<"$upload_replay_response"
echo

echo "file detail:"
curl -fsS "$BASE_URL/api/v1/legal-agent/files/$file_id"
echo

cancel_create_payload='{"input":{"text":"我准备申请劳动仲裁，但信息还没整理好。","file_ids":[]}}'
cancel_create_response="$(
  curl -fsS "$BASE_URL/api/v1/legal-agent/runs" \
    -H "Idempotency-Key: ${idempotency_prefix}-create-cancel" \
    -H 'content-type: application/json' \
    -d "$cancel_create_payload"
)"
echo "cancel test created:"
echo "$cancel_create_response"
cancel_run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$cancel_create_response")"
wait_for_run_status "$cancel_run_id" "WAITING_USER_INPUT"
cancel_payload='{"reason":"smoke user cancelled","requested_by":"smoke-reviewer"}'
cancel_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$cancel_run_id/cancel" \
  -H "Idempotency-Key: ${idempotency_prefix}-cancel-run" \
  -H 'content-type: application/json' \
  -d "$cancel_payload")"
echo "cancel test cancelled:"
echo "$cancel_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert data["run_status"] == "CANCELLED"; assert data["cancellation_status"] == "cancelled"' <<<"$cancel_response"
cancel_replay_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$cancel_run_id/cancel" \
  -H "Idempotency-Key: ${idempotency_prefix}-cancel-run" \
  -H 'content-type: application/json' \
  -d "$cancel_payload")"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["run_id"] == first["run_id"]; assert second["run_status"] == "CANCELLED"; assert second["cancellation_status"] == first["cancellation_status"]' "$cancel_response" <<<"$cancel_replay_response"
wait_for_run_status "$cancel_run_id" "CANCELLED"
echo

facts_payload='{"facts":{"applicant_name":"张三","company_name":"北京某科技有限公司","work_start_date":"2023-01-01","work_end_date":"2024-03-01","monthly_salary":15000,"unpaid_months":2,"contract_signed":false,"social_insurance_paid":false,"termination_reason":"unilateral_dismissal_without_notice","evidence_available":["salary_flow","chat_record","attendance_record"],"expected_claims":["unpaid_salary","double_salary","termination_compensation_or_damages"]}}'

user_timeout_payload='{"user_input_timeout_seconds":2,"input":{"text":"我准备申请劳动仲裁，但信息还没整理好。","file_ids":[]}}'
user_timeout_create_response="$(
  curl -fsS "$BASE_URL/api/v1/legal-agent/runs" \
    -H "Idempotency-Key: ${idempotency_prefix}-create-user-timeout" \
    -H 'content-type: application/json' \
    -d "$user_timeout_payload"
)"
echo "user input timeout created:"
echo "$user_timeout_create_response"
user_timeout_run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$user_timeout_create_response")"
wait_for_run_status "$user_timeout_run_id" "WAITING_USER_INPUT"
echo "waiting for user input timeout:"
wait_for_run_status "$user_timeout_run_id" "EXPIRED"
user_timeout_audit_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$user_timeout_run_id/audit")"
python3 -c 'import json,sys; data=json.load(sys.stdin); kinds={(row.get("metadata_json") or {}).get("kind") for row in data.get("artifacts", [])}; assert "user_input_timeout" in kinds, kinds; assert not data.get("documents"), "expired run must not output document"; print("user input timeout audit ok")' <<<"$user_timeout_audit_response"
late_facts_status="$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/api/v1/legal-agent/runs/$user_timeout_run_id/facts" \
  -H "Idempotency-Key: ${idempotency_prefix}-late-facts-user-timeout" \
  -H 'content-type: application/json' \
  -d "$facts_payload")"
if [[ "$late_facts_status" != "409" ]]; then
  echo "expected 409 for facts on expired run, got $late_facts_status" >&2
  exit 1
fi
echo

timeout_payload="$(python3 -c 'import json,sys; print(json.dumps({"output_options":{"require_human_review":True,"approval_timeout_seconds":2},"input":{"text":"我被公司无故辞退，拖欠 2 个月工资，没有签劳动合同，帮我生成劳动仲裁申请书。","file_ids":[sys.argv[1]]}}, ensure_ascii=False))' "$file_id")"
timeout_create_response="$(
  curl -fsS "$BASE_URL/api/v1/legal-agent/runs" \
    -H "Idempotency-Key: ${idempotency_prefix}-create-timeout" \
    -H 'content-type: application/json' \
    -d "$timeout_payload"
)"
echo "approval timeout created:"
echo "$timeout_create_response"
timeout_run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$timeout_create_response")"
wait_for_run_status "$timeout_run_id" "WAITING_USER_INPUT"
timeout_facts_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$timeout_run_id/facts" \
  -H "Idempotency-Key: ${idempotency_prefix}-facts-timeout" \
  -H 'content-type: application/json' \
  -d "$facts_payload")"
echo "approval timeout facts:"
echo "$timeout_facts_response"
wait_for_run_status "$timeout_run_id" "WAITING_APPROVAL"
timeout_approvals_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$timeout_run_id/approvals")"
timeout_approval_id="$(python3 -c 'import json,sys; data=json.load(sys.stdin); pending=[item for item in data.get("approvals", []) if item.get("status") == "PENDING"]; print(pending[0]["approval_id"] if pending else "")' <<<"$timeout_approvals_response")"
if [[ -z "$timeout_approval_id" ]]; then
  echo "expected pending timeout approval" >&2
  exit 1
fi
echo "waiting for approval timeout:"
wait_for_run_status "$timeout_run_id" "EXPIRED"
timeout_approvals_after="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$timeout_run_id/approvals")"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert any(item.get("status") == "EXPIRED" for item in data.get("approvals", [])), data' <<<"$timeout_approvals_after"
timeout_audit_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$timeout_run_id/audit")"
python3 -c 'import json,sys; data=json.load(sys.stdin); kinds={(row.get("metadata_json") or {}).get("kind") for row in data.get("artifacts", [])}; assert "approval_timeout" in kinds, kinds; assert not data.get("documents"), "expired run must not output document"; print("approval timeout audit ok")' <<<"$timeout_audit_response"
echo

create_payload="$(python3 -c 'import json,sys; print(json.dumps({"input":{"text":"我被公司无故辞退，拖欠 2 个月工资，没有签劳动合同，帮我生成劳动仲裁申请书。","file_ids":[sys.argv[1]]}}, ensure_ascii=False))' "$file_id")"

create_response="$(
  curl -fsS "$BASE_URL/api/v1/legal-agent/runs" \
    -H "Idempotency-Key: ${idempotency_prefix}-create-document" \
    -H 'content-type: application/json' \
    -d "$create_payload"
)"
echo "created:"
echo "$create_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert "social_insurance_paid" in data.get("missing_fields", []); assert data.get("questions"), "expected preflight questions"' <<<"$create_response"
create_replay_response="$(
  curl -fsS "$BASE_URL/api/v1/legal-agent/runs" \
    -H "Idempotency-Key: ${idempotency_prefix}-create-document" \
    -H 'content-type: application/json' \
    -d "$create_payload"
)"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["run_id"] == first["run_id"]; assert second["agentledger_run_id"] == first["agentledger_run_id"]' "$create_response" <<<"$create_replay_response"
echo

run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$create_response")"

wait_for_status() {
  local expected="$1"
  wait_for_run_status "$run_id" "$expected"
}

echo "waiting for user input:"
wait_for_status "WAITING_USER_INPUT"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert "social_insurance_paid" in data.get("missing_fields", []); assert data.get("questions"), "expected status questions"' <<<"$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id")"
echo

echo "submit facts:"
facts_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/facts" \
  -H "Idempotency-Key: ${idempotency_prefix}-facts-document" \
  -H 'content-type: application/json' \
  -d "$facts_payload")"
echo "$facts_response"
facts_replay_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/facts" \
  -H "Idempotency-Key: ${idempotency_prefix}-facts-document" \
  -H 'content-type: application/json' \
  -d "$facts_payload")"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["run_id"] == first["run_id"]; assert second["current_node"] == first["current_node"]' "$facts_response" <<<"$facts_replay_response"
echo

echo "waiting for approval:"
wait_for_status "WAITING_APPROVAL"
echo

echo "approvals:"
approvals_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/approvals")"
echo "$approvals_response"
approval_id="$(python3 -c 'import json,sys; data=json.load(sys.stdin); pending=[item for item in data.get("approvals", []) if item.get("status") == "PENDING"]; print(pending[0]["approval_id"] if pending else "")' <<<"$approvals_response")"
if [[ -z "$approval_id" ]]; then
  echo "expected pending approval" >&2
  exit 1
fi
echo

echo "approve:"
approve_payload='{"approved":true,"approver":"smoke-reviewer","reason":"smoke approval"}'
approve_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/approvals/$approval_id" \
  -H "Idempotency-Key: ${idempotency_prefix}-approve-document" \
  -H 'content-type: application/json' \
  -d "$approve_payload")"
echo "$approve_response"
approve_replay_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/approvals/$approval_id" \
  -H "Idempotency-Key: ${idempotency_prefix}-approve-document" \
  -H 'content-type: application/json' \
  -d "$approve_payload")"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["approval_id"] == first["approval_id"]; assert second["approval_status"] == first["approval_status"]' "$approve_response" <<<"$approve_replay_response"
echo

echo "waiting for completion:"
wait_for_status "COMPLETED"
echo

echo "result:"
result_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/result")"
echo "$result_response"
python3 -c 'import json,sys; result=json.load(sys.stdin).get("result") or {}; assert result.get("amount_calculation", {}).get("status") == "calculated"; assert result.get("review_result", {}).get("citation_check") == "passed"' <<<"$result_response"
echo

echo "audit:"
audit_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/audit")"
python3 -c 'import json,sys; data=json.load(sys.stdin); summary=data.get("summary") or {}; artifacts=data.get("artifacts", []); ledger=data.get("tool_ledger", []); tools={row.get("tool_name") for row in ledger if row.get("status") == "SUCCEEDED"}; kinds={(row.get("metadata_json") or {}).get("kind") for row in artifacts}; required={"fact_check_result","retrieval_evidence_pack","draft_document","review_result","approval_decision","generated_legal_document"}; assert data.get("agentledger_run_id"); assert summary.get("event_count", 0) > 0; assert {"salary_calculator","document_template_tool","format_checker","citation_checker"} <= tools; assert required <= kinds, f"missing artifact kinds: {sorted(required-kinds)}"; assert any(row.get("status") == "APPROVED" for row in data.get("approvals", [])); print(json.dumps(summary, ensure_ascii=False, sort_keys=True))' <<<"$audit_response"
echo

echo "replay:"
replay_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/replay")"
python3 -c 'import json,sys; data=json.load(sys.stdin); kinds={artifact.get("metadata", {}).get("kind") for artifact in data.get("artifacts", [])}; calls={call.get("tool_name") for call in data.get("tool_calls", [])}; assert data.get("timeline"); assert {"salary_calculator","document_template_tool","format_checker","citation_checker"} <= calls; assert {"retrieval_evidence_pack","draft_document","review_result","generated_legal_document"} <= kinds; print("replay ok")' <<<"$replay_response"
echo

document_id="$(python3 -c 'import json,sys; result=json.load(sys.stdin).get("result") or {}; print(result.get("document_id") or (result.get("document") or {}).get("document_id") or "")' <<<"$result_response")"
if [[ -z "$document_id" ]]; then
  echo "expected result document_id" >&2
  exit 1
fi

echo "documents:"
documents_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/documents")"
echo "$documents_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); doc_id=sys.argv[1]; assert any(doc.get("document_id") == doc_id for doc in data.get("documents", [])), f"missing document {doc_id}"' "$document_id" <<<"$documents_response"
echo

echo "document detail:"
document_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/documents/$document_id")"
echo "$document_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); markdown=data.get("markdown") or ""; assert data.get("document_id") == sys.argv[1]; assert "# 劳动人事争议仲裁申请书" in markdown; assert "uploaded-file://" in markdown; assert "工资流水" in markdown' "$document_id" <<<"$document_response"
echo

echo "document markdown:"
markdown_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/documents/$document_id/markdown")"
echo "$markdown_response"
python3 -c 'import sys; content=sys.stdin.read(); assert "# 劳动人事争议仲裁申请书" in content' <<<"$markdown_response"
echo

echo "document docx:"
curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/documents/$document_id/docx" -o "$docx_file"
python3 -c 'import sys,zipfile,xml.etree.ElementTree as ET; path=sys.argv[1]; z=zipfile.ZipFile(path); xml=z.read("word/document.xml"); text="".join(node.text or "" for node in ET.fromstring(xml).iter()); assert "劳动人事争议仲裁申请书" in text; assert "工资流水" in text' "$docx_file"
echo "docx ok: $docx_file"

echo "evidence:"
evidence_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/evidence")"
echo "$evidence_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); items=data.get("evidence_pack") or []; assert any(item.get("source_type") == "law" for item in items); assert any(item.get("source_type") == "user_material" for item in items); assert any(item.get("supported_claim") for item in items)' <<<"$evidence_response"
echo

echo "case search:"
case_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/cases/search" -H 'content-type: application/json' -d '{"claims":["未签劳动合同","拖欠工资","仲裁时效"],"region":"CN","top_k":5}')"
echo "$case_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); cases=data.get("cases") or []; assert cases, "expected cases"; assert any(case.get("case_id") for case in cases); assert any(case.get("source_url") for case in cases)' <<<"$case_response"

case_payload="$(python3 -c 'import json,sys; print(json.dumps({"task_type":"case_analysis","output_options":{"document_type":"labor_dispute_case_analysis","format":"markdown","require_human_review":True},"input":{"text":"请分析这个劳动争议案件的争议焦点、可能请求、法律依据和类案风险。","file_ids":[sys.argv[1]]}}, ensure_ascii=False))' "$file_id")"

case_create_response="$(
  curl -fsS "$BASE_URL/api/v1/legal-agent/runs" \
    -H "Idempotency-Key: ${idempotency_prefix}-create-case-analysis" \
    -H 'content-type: application/json' \
    -d "$case_payload"
)"
echo "case analysis created:"
echo "$case_create_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert "social_insurance_paid" in data.get("missing_fields", []); assert data.get("questions"), "expected case analysis preflight questions"' <<<"$case_create_response"
case_create_replay_response="$(
  curl -fsS "$BASE_URL/api/v1/legal-agent/runs" \
    -H "Idempotency-Key: ${idempotency_prefix}-create-case-analysis" \
    -H 'content-type: application/json' \
    -d "$case_payload"
)"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["run_id"] == first["run_id"]; assert second["agentledger_run_id"] == first["agentledger_run_id"]' "$case_create_response" <<<"$case_create_replay_response"
echo

run_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])' <<<"$case_create_response")"

echo "case analysis waiting for user input:"
wait_for_status "WAITING_USER_INPUT"
python3 -c 'import json,sys; data=json.load(sys.stdin); assert "social_insurance_paid" in data.get("missing_fields", []); assert data.get("questions"), "expected case analysis status questions"' <<<"$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id")"
echo

echo "case analysis submit facts:"
case_facts_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/facts" \
  -H "Idempotency-Key: ${idempotency_prefix}-facts-case-analysis" \
  -H 'content-type: application/json' \
  -d "$facts_payload")"
echo "$case_facts_response"
case_facts_replay_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/facts" \
  -H "Idempotency-Key: ${idempotency_prefix}-facts-case-analysis" \
  -H 'content-type: application/json' \
  -d "$facts_payload")"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["run_id"] == first["run_id"]; assert second["current_node"] == first["current_node"]' "$case_facts_response" <<<"$case_facts_replay_response"
echo

echo "case analysis waiting for approval:"
wait_for_status "WAITING_APPROVAL"
echo

echo "case analysis approvals:"
case_approvals_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/approvals")"
echo "$case_approvals_response"
case_approval_id="$(python3 -c 'import json,sys; data=json.load(sys.stdin); pending=[item for item in data.get("approvals", []) if item.get("status") == "PENDING"]; print(pending[0]["approval_id"] if pending else "")' <<<"$case_approvals_response")"
if [[ -z "$case_approval_id" ]]; then
  echo "expected pending case analysis approval" >&2
  exit 1
fi
echo

echo "case analysis approve:"
case_approve_payload='{"approved":true,"approver":"smoke-reviewer","reason":"case analysis smoke approval"}'
case_approve_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/approvals/$case_approval_id" \
  -H "Idempotency-Key: ${idempotency_prefix}-approve-case-analysis" \
  -H 'content-type: application/json' \
  -d "$case_approve_payload")"
echo "$case_approve_response"
case_approve_replay_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/approvals/$case_approval_id" \
  -H "Idempotency-Key: ${idempotency_prefix}-approve-case-analysis" \
  -H 'content-type: application/json' \
  -d "$case_approve_payload")"
python3 -c 'import json,sys; first=json.loads(sys.argv[1]); second=json.load(sys.stdin); assert second["approval_id"] == first["approval_id"]; assert second["approval_status"] == first["approval_status"]' "$case_approve_response" <<<"$case_approve_replay_response"
echo

echo "case analysis waiting for completion:"
wait_for_status "COMPLETED"
echo

echo "case analysis result:"
case_result_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/result")"
echo "$case_result_response"
python3 -c 'import json,sys; result=json.load(sys.stdin).get("result") or {}; doc=result.get("document") or {}; assert doc.get("document_type") == "labor_dispute_case_analysis"; assert result.get("review_result", {}).get("citation_check") == "passed"' <<<"$case_result_response"
echo

echo "case analysis audit:"
case_audit_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/audit")"
python3 -c 'import json,sys; data=json.load(sys.stdin); ledger=data.get("tool_ledger", []); tools={row.get("tool_name") for row in ledger if row.get("status") == "SUCCEEDED"}; assert {"salary_calculator","case_search_api","document_template_tool","format_checker","citation_checker"} <= tools; assert any((row.get("metadata_json") or {}).get("kind") == "draft_document" for row in data.get("artifacts", [])); print("case audit ok")' <<<"$case_audit_response"
echo

echo "metrics after runs:"
metrics_after_response="$(curl -fsS "$BASE_URL/metrics")"
python3 -c 'import sys; data=sys.stdin.read(); required=["legal_agent_runs_total","legal_agent_approvals_total","legal_agent_generated_documents_total","rag_document_chunks_total","agentledger_tool_ledger_total","agentledger_artifacts_total","agentledger_events_total"]; missing=[item for item in required if item not in data]; assert not missing, missing; assert "citation_checker" in data; assert "document_template_tool" in data; assert "case_search_api" in data; print("metrics after runs ok")' <<<"$metrics_after_response"
echo

case_document_id="$(python3 -c 'import json,sys; result=json.load(sys.stdin).get("result") or {}; print(result.get("document_id") or (result.get("document") or {}).get("document_id") or "")' <<<"$case_result_response")"
if [[ -z "$case_document_id" ]]; then
  echo "expected case analysis document_id" >&2
  exit 1
fi

echo "case analysis document detail:"
case_document_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/documents/$case_document_id")"
echo "$case_document_response"
python3 -c 'import json,sys; data=json.load(sys.stdin); markdown=data.get("markdown") or ""; assert data.get("document_id") == sys.argv[1]; assert "# 劳动争议案情分析报告" in markdown; assert "## 类案参考" in markdown; assert "# 劳动人事争议仲裁申请书" not in markdown' "$case_document_id" <<<"$case_document_response"
echo

echo "case analysis document markdown:"
case_markdown_response="$(curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/documents/$case_document_id/markdown")"
echo "$case_markdown_response"
python3 -c 'import sys; content=sys.stdin.read(); assert "# 劳动争议案情分析报告" in content; assert "## 类案参考" in content' <<<"$case_markdown_response"
echo

echo "case analysis document docx:"
curl -fsS "$BASE_URL/api/v1/legal-agent/runs/$run_id/documents/$case_document_id/docx" -o "$docx_file"
python3 -c 'import sys,zipfile,xml.etree.ElementTree as ET; path=sys.argv[1]; z=zipfile.ZipFile(path); xml=z.read("word/document.xml"); text="".join(node.text or "" for node in ET.fromstring(xml).iter()); assert "劳动争议案情分析报告" in text; assert "类案参考" in text' "$docx_file"
echo "case analysis docx ok: $docx_file"
