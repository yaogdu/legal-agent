from __future__ import annotations

import json
from typing import Any

from temporalio import activity

from legal_agent.core.config import load_settings
from legal_agent.core.enums import NodeName, NodeStatus, RunStatus
from legal_agent.core.claims import expected_claim_types
from legal_agent.core.facts import (
    infer_facts_from_input,
    merge_inferred_claims,
    missing_fact_fields,
    question_groups_for_missing_fields,
    questions_for_missing_fields,
)
from legal_agent.db.repository import RunRepository
from legal_agent.document_templates.docx_export import write_markdown_docx
from legal_agent.llm.client import extract_labor_claims_result, generate_case_analysis_markdown_result, generate_labor_arbitration_markdown_result
from legal_agent.rag.ingest import backfill_missing_embeddings
from legal_agent.runtime.agentledger import (
    call_case_search_tool,
    call_citation_checker_tool,
    call_document_template_tool,
    call_format_checker_tool,
    call_salary_calculator_tool,
    create_agentledger_artifact,
    decide_agentledger_approval,
    patch_agentledger_state,
    request_agentledger_approval,
)
from legal_agent.runtime.tracing import trace_span


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": payload["run_id"],
        "agentledger_run_id": payload["agentledger_run_id"],
        "temporal_workflow_id": payload.get("temporal_workflow_id"),
    }


@activity.defn
async def health_check_activity(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "ok",
        "task_queue": payload.get("task_queue"),
        "worker": payload.get("worker") or "legal-agent-worker",
    }


@activity.defn
async def embedding_backfill_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    with trace_span(settings, "embedding.backfill", {"node": "EMBEDDING", "task_queue": payload.get("task_queue")}):
        return backfill_missing_embeddings(settings, limit=int(payload.get("limit") or 100))


def _record_agentledger_artifact(
    settings: Any,
    payload: dict[str, Any],
    *,
    name: str,
    kind: str,
    value: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    return create_agentledger_artifact(
        settings,
        agentledger_run_id=payload["agentledger_run_id"],
        name=name,
        value=value,
        metadata={
            "kind": kind,
            "legal_agent_run_id": payload["run_id"],
            "temporal_workflow_id": payload.get("temporal_workflow_id"),
            "task_type": payload.get("task_type"),
            **(metadata or {}),
        },
    )


LEGAL_SOURCE_TYPES = {"law", "judicial_interpretation", "administrative_regulation", "department_rule", "regulation"}


def _retrieve_evidence(repo: RunRepository, payload: dict[str, Any], facts: dict[str, Any]) -> list[dict[str, Any]]:
    queries = _retrieval_queries(facts, str(payload.get("task_type") or "document_generation"))
    seen_chunks: set[str] = set()
    evidence_pack: list[dict[str, Any]] = []
    jurisdiction = payload.get("jurisdiction") or "CN"
    for query in queries:
        for chunk in repo.search_legal_chunks(query=query, jurisdiction=jurisdiction, limit=4):
            chunk_id = chunk["chunk_id"]
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            evidence_pack.append(
                {
                    "evidence_id": f"ev_{len(evidence_pack) + 1:03d}",
                    "chunk_id": chunk_id,
                    "source_type": chunk["doc_type"],
                    "authority_level": chunk["authority_level"],
                    "source_name": chunk["title"],
                    "source_url": chunk["source_url"],
                    "citation_anchor": chunk["citation_anchor"],
                    "quote": chunk["content"],
                    "supported_claim": _supported_claim(chunk),
                    "score": float(chunk["score"] or 0),
                    "retrieval_method": chunk["retrieval_method"],
                    "metadata": {
                        "doc_id": chunk["doc_id"],
                        "query": query,
                        "metadata": chunk.get("metadata_json") or {},
                    },
                }
            )
    return evidence_pack


def _retrieve_user_material_evidence(
    repo: RunRepository,
    *,
    file_ids: list[str],
    facts: dict[str, Any],
    start_index: int,
) -> list[dict[str, Any]]:
    if not file_ids:
        return []
    queries = [
        "工资 流水 聊天记录 考勤 解除通知 劳动合同 offer 工牌 社保",
        *_retrieval_queries(facts, "document_generation"),
    ]
    seen_chunks: set[str] = set()
    evidence_pack: list[dict[str, Any]] = []
    for query in queries:
        for chunk in repo.search_user_material_chunks(file_ids=file_ids, query=query, limit=4):
            chunk_id = chunk["chunk_id"]
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            evidence_pack.append(
                {
                    "evidence_id": f"ev_{start_index + len(evidence_pack) + 1:03d}",
                    "chunk_id": chunk_id,
                    "source_type": "user_material",
                    "authority_level": "U0",
                    "source_name": chunk["original_filename"],
                    "source_url": f"uploaded-file://{chunk['file_id']}",
                    "citation_anchor": chunk["citation_anchor"],
                    "quote": chunk["content"],
                    "supported_claim": "用户上传材料可支持本 run 内事实核验，不能单独作为法律结论依据。",
                    "score": float(chunk["score"] or 0),
                    "retrieval_method": "user_material_full_text",
                    "metadata": {
                        "file_id": chunk["file_id"],
                        "sha256": chunk["sha256"],
                        "storage_path": chunk["storage_path"],
                        "metadata": chunk.get("metadata_json") or {},
                    },
                }
            )
    return evidence_pack


def _retrieval_queries(facts: dict[str, Any], task_type: str = "document_generation") -> list[str]:
    queries = ["劳动争议 仲裁 劳动报酬"]
    claim_types = set(expected_claim_types(facts, task_type))
    if task_type == "case_analysis":
        queries.append("劳动争议 案情分析 类案 争议焦点")
    else:
        queries.append("劳动人事争议仲裁申请书")
    if facts.get("contract_signed") is False or "double_salary" in claim_types:
        queries.append("未签 书面劳动合同")
    if facts.get("unpaid_months") or "unpaid_salary" in claim_types:
        queries.append("拖欠 工资 劳动报酬")
    if facts.get("termination_reason") or "illegal_termination_damages" in claim_types:
        queries.append("解除 劳动合同 赔偿")
    if "economic_compensation" in claim_types:
        queries.append("解除 劳动合同 经济补偿 N+1")
    if "year_end_bonus" in claim_types:
        queries.append("年终奖 劳动报酬 发放 条件")
    if "overtime_pay" in claim_types:
        queries.append("加班费 加班工资 考勤 证据")
    if "unused_annual_leave_pay" in claim_types:
        queries.append("未休年休假 工资报酬")
    return queries


def _case_search_query(facts: dict[str, Any], evidence_pack: list[dict[str, Any]]) -> str:
    claims = " ".join(str(item) for item in facts.get("expected_claims") or [])
    evidence_terms = " ".join(
        str(evidence.get("citation_anchor") or evidence.get("supported_claim") or "")
        for evidence in evidence_pack
        if evidence.get("source_type") in {"case", "typical_case", "guiding_case"}
    )
    base = "劳动争议 类案 争议焦点 仲裁时效 拖欠工资 未签劳动合同 违法解除"
    return " ".join(part for part in [base, claims, evidence_terms] if part).strip()


def _build_execution_plan(payload: dict[str, Any], row: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    task_type = str(payload.get("task_type") or row.get("task_type") or "document_generation")
    document_type = "labor_dispute_case_analysis" if task_type == "case_analysis" else "labor_arbitration_application"
    expected_outputs = ["evidence_pack", "amount_calculation", "review_result"]
    required_tools = ["salary_calculator", "citation_checker", "format_checker"]
    if task_type == "case_analysis":
        required_tools.extend(["case_search_api", "document_template_tool"])
        expected_outputs.extend(["case_references", "case_analysis_report"])
    else:
        required_tools.append("document_template_tool")
        expected_outputs.append("labor_arbitration_application")
    risk_gates = [
        {
            "gate": "human_review_before_output",
            "risk_level": row.get("risk_level") or "L2",
            "reason": "正式输出法律文书或分析报告草稿前需要人工复核。",
        }
    ]
    if facts.get("expected_claims"):
        expected_outputs.append("structured_claims")
    return {
        "task_type": task_type,
        "legal_domain": payload.get("legal_domain") or row.get("legal_domain") or "labor_dispute",
        "jurisdiction": payload.get("jurisdiction") or row.get("jurisdiction") or "CN",
        "document_type": document_type,
        "steps": [
            {"node": "RETRIEVE", "purpose": "检索法规、模板、类案和用户材料，组装 evidence pack。"},
            {"node": "TOOL", "purpose": "通过 AgentLedger ToolGateway 调用金额计算等工具。"},
            {"node": "DRAFT", "purpose": "基于本地模板和可选 LLM 生成草稿。"},
            {"node": "REVIEW", "purpose": "校验事实、引用、格式和风险提示。"},
            {"node": "APPROVAL", "purpose": "人工复核后才允许输出正式草稿 artifact。"},
            {"node": "OUTPUT", "purpose": "写入结构化结果、Markdown、DOCX 和 AgentLedger artifact。"},
        ],
        "required_tools": required_tools,
        "risk_gates": risk_gates,
        "expected_outputs": expected_outputs,
    }


def _supported_claim(chunk: dict[str, Any]) -> str:
    metadata = dict(chunk.get("metadata_json") or {})
    default = "该资料已进入本地法律库，具体支持关系需人工复核。"
    return str(metadata.get("supported_claim") or default).strip() or default


def _requires_human_approval(row: dict[str, Any], review_result: dict[str, Any]) -> bool:
    input_json = dict(row.get("input_json") or {})
    output_options = dict(input_json.get("output_options") or {})
    require_human_review = bool(output_options.get("require_human_review", True))
    risk_level = str(row.get("risk_level") or "L2")
    high_risk = risk_level in {"L3", "L4", "L5"}
    review_failed = not bool(review_result.get("passed", False))
    return require_human_review or high_risk or review_failed


def _review_draft(draft: dict[str, Any], evidence_pack: list[dict[str, Any]], missing_fields: list[str]) -> dict[str, Any]:
    markdown = str(draft.get("markdown") or "")
    required_sections = ["## 申请人", "## 被申请人", "## 仲裁请求", "## 事实与理由", "## 证据和证据来源", "## 法律依据"]
    missing_sections = [section for section in required_sections if section not in markdown]
    legal_evidence_anchors = {
        str(evidence.get("citation_anchor"))
        for evidence in evidence_pack
        if evidence.get("source_type") in LEGAL_SOURCE_TYPES and evidence.get("citation_anchor")
    }
    missing_citations = []
    for line in draft.get("legal_basis") or []:
        raw_anchor = str(line).split("：", 1)[0].strip()
        anchor = raw_anchor.split(". ", 1)[1].strip() if ". " in raw_anchor else raw_anchor
        if anchor and anchor not in legal_evidence_anchors:
            missing_citations.append(anchor)
    unsupported_claims = []
    claims_text = "\n".join(draft.get("claims") or [])
    evidence_text = "\n".join(str(evidence.get("supported_claim") or evidence.get("quote") or "") for evidence in evidence_pack)
    if "未依法签订书面劳动合同" in claims_text and "书面劳动合同" not in evidence_text:
        unsupported_claims.append("未签书面劳动合同相关责任缺少法律依据支撑。")
    if "拖欠工资" in claims_text and not any("劳动报酬" in str(evidence.get("quote") or "") for evidence in evidence_pack):
        unsupported_claims.append("拖欠工资请求缺少劳动报酬争议依据支撑。")
    return {
        "passed": not missing_fields and not missing_sections and not missing_citations and not unsupported_claims,
        "fact_consistency": "pending" if missing_fields else "passed",
        "citation_check": "passed" if not missing_citations else "failed",
        "format_check": "passed" if not missing_sections else "failed",
        "missing_sections": missing_sections,
        "missing_citations": missing_citations,
        "unsupported_claims": unsupported_claims,
    }


def _review_case_analysis(draft: dict[str, Any], evidence_pack: list[dict[str, Any]], missing_fields: list[str]) -> dict[str, Any]:
    markdown = str(draft.get("markdown") or "")
    required_sections = ["## 案情摘要", "## 争议焦点", "## 可能仲裁请求", "## 法律依据", "## 类案参考", "## 金额测算", "## 证据与待补充材料", "## 风险提示"]
    missing_sections = [section for section in required_sections if section not in markdown]
    legal_evidence_count = sum(1 for evidence in evidence_pack if evidence.get("source_type") in LEGAL_SOURCE_TYPES)
    case_reference_count = sum(1 for evidence in evidence_pack if evidence.get("source_type") in {"case", "typical_case", "guiding_case"})
    unsupported_claims = []
    if legal_evidence_count == 0:
        unsupported_claims.append("案情分析缺少法律依据支撑。")
    return {
        "passed": not missing_fields and not missing_sections and not unsupported_claims,
        "fact_consistency": "pending" if missing_fields else "passed",
        "citation_check": "passed" if legal_evidence_count > 0 else "failed",
        "format_check": "passed" if not missing_sections else "failed",
        "missing_sections": missing_sections,
        "missing_citations": [],
        "unsupported_claims": unsupported_claims,
        "case_reference_count": case_reference_count,
    }


def _required_sections_for_document(document_type: str, task_type: str | None = None) -> list[str]:
    if document_type == "labor_dispute_case_analysis" or task_type == "case_analysis":
        return ["## 案情摘要", "## 争议焦点", "## 可能仲裁请求", "## 法律依据", "## 类案参考", "## 金额测算", "## 证据与待补充材料", "## 风险提示"]
    return ["## 申请人", "## 被申请人", "## 仲裁请求", "## 事实与理由", "## 证据和证据来源", "## 法律依据"]


def _merge_format_checker_result(review: dict[str, Any], format_result: dict[str, Any]) -> dict[str, Any]:
    review["format_check"] = "passed" if format_result.get("passed") else "failed"
    review["missing_sections"] = list(format_result.get("missing_sections") or [])
    review["format_checker_result"] = format_result
    return _recompute_review_passed(review)


def _merge_citation_checker_result(review: dict[str, Any], citation_result: dict[str, Any]) -> dict[str, Any]:
    review["citation_check"] = "passed" if citation_result.get("passed") else "failed"
    review["missing_citations"] = list(citation_result.get("missing_citations") or [])
    review["citation_checker_result"] = citation_result
    return _recompute_review_passed(review)


def _recompute_review_passed(review: dict[str, Any]) -> dict[str, Any]:
    review["passed"] = (
        review.get("fact_consistency") == "passed"
        and review.get("citation_check") == "passed"
        and review.get("format_check") == "passed"
        and not list(review.get("unsupported_claims") or [])
    )
    return review


@activity.defn
async def classify_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    with trace_span(settings, "classify", _metadata(payload)):
        patch_agentledger_state(settings, payload["agentledger_run_id"], {"classification": {"task_type": payload["task_type"], "legal_domain": payload["legal_domain"]}}, "classify activity")
        repo.update_status(
            payload["run_id"],
            run_status=RunStatus.RUNNING,
            current_node=NodeName.FACT_CHECK,
            current_node_status=NodeStatus.RUNNING,
        )
        return payload


@activity.defn
async def fact_check_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    row = repo.get_run(payload["run_id"])
    facts = dict(row.get("facts_json") or {}) if row else {}
    if payload.get("signal_facts"):
        facts = repo.merge_facts(payload["run_id"], dict(payload["signal_facts"]))
    input_json = dict(row.get("input_json") or {}) if row else {}
    text = ((input_json.get("input") or {}).get("text") or "").strip()
    file_ids = list((input_json.get("input") or {}).get("file_ids") or [])
    inferred = {
        key: value
        for key, value in infer_facts_from_input(text, file_ids).items()
        if key not in facts or facts[key] in (None, "", [], {})
    }
    claim_extraction = await extract_labor_claims_result(
        settings,
        user_input=text,
        metadata={**_metadata(payload), "node": "FACT_CHECK"},
    )
    claim_patch = merge_inferred_claims({**facts, **inferred}, claim_extraction.claims)
    for key, value in claim_patch.items():
        if key not in facts or facts[key] in (None, "", [], {}):
            inferred[key] = value
    if inferred:
        facts = repo.merge_facts(payload["run_id"], inferred, source_type="system_inferred")
    missing = missing_fact_fields(facts, str(payload.get("task_type") or "document_generation"))
    questions = questions_for_missing_fields(missing)
    question_groups = question_groups_for_missing_fields(missing)
    patch_agentledger_state(
        settings,
        payload["agentledger_run_id"],
        {"facts": facts, "missing_fields": missing, "questions": questions, "question_groups": question_groups},
        "fact check activity",
    )
    _record_agentledger_artifact(
        settings,
        payload,
        name="fact_check_result.json",
        kind="fact_check_result",
        value={
            "run_id": payload["run_id"],
            "agentledger_run_id": payload["agentledger_run_id"],
            "facts": facts,
            "missing_fields": missing,
            "questions": questions,
            "question_groups": question_groups,
            "signal_facts": dict(payload.get("signal_facts") or {}),
            "inferred_facts": inferred,
            "claim_extraction": claim_extraction.to_audit_payload(),
        },
        metadata={
            "node": "FACT_CHECK",
            "has_missing_fields": bool(missing),
            "from_signal": bool(payload.get("signal_facts")),
        },
    )
    if missing:
        repo.update_status(
            payload["run_id"],
            run_status=RunStatus.WAITING_USER_INPUT,
            current_node=NodeName.ASK_USER,
            current_node_status=NodeStatus.WAITING,
            missing_fields=missing,
        )
        return {**payload, "can_continue": False, "missing_fields": missing, "questions": questions, "question_groups": question_groups}
    repo.update_status(
        payload["run_id"],
        run_status=RunStatus.RUNNING,
        current_node=NodeName.PLAN,
        current_node_status=NodeStatus.RUNNING,
        missing_fields=[],
    )
    return {**payload, "can_continue": True, "missing_fields": [], "questions": [], "question_groups": []}


@activity.defn
async def user_input_timeout_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    missing_fields = list(payload.get("missing_fields") or [])
    questions = list(payload.get("questions") or [])
    question_groups = list(payload.get("question_groups") or [])
    timeout_seconds = int(payload.get("user_input_timeout_seconds") or settings.user_input_timeout_seconds)
    timeout = {
        "status": "EXPIRED",
        "expire_reason": "user_input_timeout",
        "timeout_seconds": timeout_seconds,
        "missing_fields": missing_fields,
        "questions": questions,
        "question_groups": question_groups,
    }
    result = {
        "expire_reason": "user_input_timeout",
        "missing_fields": missing_fields,
        "questions": questions,
        "question_groups": question_groups,
    }
    patch_agentledger_state(
        settings,
        payload["agentledger_run_id"],
        {"user_input": timeout, "result": result, "expire_reason": "user_input_timeout"},
        "user input timeout activity",
    )
    _record_agentledger_artifact(
        settings,
        payload,
        name="user_input_timeout.json",
        kind="user_input_timeout",
        value={
            "run_id": payload["run_id"],
            "agentledger_run_id": payload["agentledger_run_id"],
            "timeout": timeout,
        },
        metadata={
            "node": "ASK_USER",
            "expire_reason": "user_input_timeout",
            "missing_field_count": len(missing_fields),
        },
    )
    repo.update_status(
        payload["run_id"],
        run_status=RunStatus.EXPIRED,
        current_node=NodeName.ASK_USER,
        current_node_status=NodeStatus.FAILED,
        missing_fields=missing_fields,
        result_summary=result,
        last_error="user_input_timeout",
    )
    return {**payload, "can_continue": False, "user_input": timeout, "result": result}


@activity.defn
async def plan_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    with trace_span(settings, "plan", _metadata(payload)):
        row = repo.get_run(payload["run_id"]) or {}
        facts = dict(row.get("facts_json") or {})
        execution_plan = _build_execution_plan(payload, row, facts)
        patch_agentledger_state(
            settings,
            payload["agentledger_run_id"],
            {"plan": execution_plan},
            "plan activity",
        )
        repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.RETRIEVE, current_node_status=NodeStatus.RUNNING)
        return {**payload, "execution_plan": execution_plan}


@activity.defn
async def retrieve_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    with trace_span(settings, "retrieve", _metadata(payload)):
        row = repo.get_run(payload["run_id"]) or {}
        facts = dict(row.get("facts_json") or {})
        evidence_pack = _retrieve_evidence(repo, payload, facts)
        input_json = dict(row.get("input_json") or {})
        file_ids = list((input_json.get("input") or {}).get("file_ids") or [])
        evidence_pack.extend(
            _retrieve_user_material_evidence(
                repo,
                file_ids=file_ids,
                facts=facts,
                start_index=len(evidence_pack),
            )
        )
        repo.replace_retrieval_evidence(payload["run_id"], evidence_pack)
        patch_agentledger_state(
            settings,
            payload["agentledger_run_id"],
            {"retrieval": {"status": "succeeded", "evidence_pack": evidence_pack}},
            "retrieve activity",
        )
        _record_agentledger_artifact(
            settings,
            payload,
            name="retrieval_evidence_pack.json",
            kind="retrieval_evidence_pack",
            value={
                "run_id": payload["run_id"],
                "agentledger_run_id": payload["agentledger_run_id"],
                "task_type": payload.get("task_type"),
                "jurisdiction": payload.get("jurisdiction") or row.get("jurisdiction") or "CN",
                "evidence_count": len(evidence_pack),
                "evidence_pack": evidence_pack,
            },
            metadata={
                "node": "RETRIEVE",
                "evidence_count": len(evidence_pack),
            },
        )
        repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.TOOL, current_node_status=NodeStatus.RUNNING)
        return {**payload, "evidence_pack": evidence_pack}


@activity.defn
async def tool_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    row = repo.get_run(payload["run_id"]) or {}
    facts = dict(row.get("facts_json") or {})
    evidence_pack = list(payload.get("evidence_pack") or [])
    task_type = str(payload.get("task_type") or row.get("task_type") or "document_generation")
    legal_evidence_refs = [
        str(evidence["evidence_id"])
        for evidence in evidence_pack
        if evidence.get("source_type") in {"law", "judicial_interpretation"}
    ]
    with trace_span(settings, "tool.salary_calculator", _metadata(payload)):
        amount_calculation = await call_salary_calculator_tool(
            settings,
            agentledger_run_id=payload["agentledger_run_id"],
            args={
                "monthly_salary": facts.get("monthly_salary"),
                "daily_wage": facts.get("daily_wage"),
                "unpaid_months": facts.get("unpaid_months"),
                "work_start_date": facts.get("work_start_date"),
                "work_end_date": facts.get("work_end_date"),
                "contract_signed": facts.get("contract_signed"),
                "termination_reason": facts.get("termination_reason"),
                "company_offer": facts.get("company_offer"),
                "requested_termination_compensation": facts.get("requested_termination_compensation"),
                "year_end_bonus_amount": facts.get("year_end_bonus_amount"),
                "overtime_hours": facts.get("overtime_hours"),
                "rest_day_overtime_hours": facts.get("rest_day_overtime_hours"),
                "statutory_holiday_overtime_hours": facts.get("statutory_holiday_overtime_hours"),
                "annual_leave_entitlement_days": facts.get("annual_leave_entitlement_days"),
                "annual_leave_taken_days": facts.get("annual_leave_taken_days"),
                "claims": facts.get("claims") or facts.get("expected_claims") or [],
                "jurisdiction": payload.get("jurisdiction") or row.get("jurisdiction") or "CN",
                "calculation_items": expected_claim_types(facts, task_type),
                "evidence_refs": ["user_facts"],
                "legal_evidence_refs": legal_evidence_refs,
                "_logical_operation": "salary_calculator:v1",
            },
        )
    case_search_result: dict[str, Any] | None = None
    if task_type == "case_analysis":
        with trace_span(settings, "tool.case_search_api", _metadata(payload)):
            case_search_result = await call_case_search_tool(
                settings,
                agentledger_run_id=payload["agentledger_run_id"],
                args={
                    "query": _case_search_query(facts, evidence_pack),
                    "jurisdiction": payload.get("jurisdiction") or row.get("jurisdiction") or "CN",
                    "top_k": 5,
                    "_logical_operation": "case_search_api:v1",
                },
            )
    tool_results: dict[str, Any] = {"salary_calculator": amount_calculation}
    if case_search_result is not None:
        tool_results["case_search_api"] = case_search_result
    with trace_span(settings, "tool.results", _metadata(payload)):
        patch_agentledger_state(
            settings,
            payload["agentledger_run_id"],
            {"tool_results": tool_results},
            "tool activity",
        )
        repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.DRAFT, current_node_status=NodeStatus.RUNNING)
        result_payload = {**payload, "amount_calculation": amount_calculation}
        if case_search_result is not None:
            result_payload["case_search_result"] = case_search_result
        return result_payload


@activity.defn
async def draft_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    row = repo.get_run(payload["run_id"]) or {}
    facts = dict(row.get("facts_json") or {})
    input_json = dict(row.get("input_json") or {})
    user_input = str((input_json.get("input") or {}).get("text") or "")
    evidence_pack = list(payload.get("evidence_pack") or [])
    amount_calculation = dict(payload.get("amount_calculation") or {})
    task_type = str(payload.get("task_type") or row.get("task_type") or "document_generation")
    with trace_span(settings, "tool.document_template_tool", {**_metadata(payload), "node": "DRAFT", "task_type": task_type}):
        template_result = await call_document_template_tool(
            settings,
            agentledger_run_id=payload["agentledger_run_id"],
            args={
                "task_type": task_type,
                "facts": facts,
                "evidence_pack": evidence_pack,
                "amount_calculation": amount_calculation,
            },
        )
    fields = dict(template_result.get("fields") or {})
    if task_type == "case_analysis":
        llm_result = await generate_case_analysis_markdown_result(
            settings,
            user_input=user_input,
            facts=facts,
            evidence_pack=evidence_pack,
            amount_calculation=amount_calculation,
            template_markdown=str(template_result.get("markdown") or ""),
            metadata={**_metadata(payload), "node": "DRAFT", "task_type": task_type},
        )
        llm_markdown = llm_result.markdown
        markdown = llm_markdown or str(template_result.get("markdown") or "")
        draft = {
            "template_id": template_result["template_id"],
            "document_type": template_result["document_type"],
            "title": template_result["title"],
            "format": template_result["format"],
            "available_formats": ["markdown", "docx"],
            "generation_mode": "llm" if llm_markdown else "template",
            "llm_provider": settings.llm_provider if llm_markdown else None,
            "llm_model": settings.llm_model if llm_markdown else None,
            "markdown": markdown,
            "applicant": {"name": facts.get("applicant_name", "待补充")},
            "respondent": {"name": facts.get("company_name", "待补充")},
            "claims": str(fields["claims_md"]).splitlines(),
            "case_summary": str(fields["case_summary_md"]).splitlines(),
            "issues": str(fields["issues_md"]).splitlines(),
            "legal_basis": str(fields["legal_basis_md"]).splitlines(),
            "case_references": str(fields["case_references_md"]).splitlines(),
            "amount_calculation": str(fields["amount_calculation_md"]).splitlines(),
            "evidence_list": str(fields["evidence_list_md"]).splitlines(),
            "pending_fields": list(template_result.get("pending_fields") or []),
            "risk_notice": str(fields["risk_notice_md"]).splitlines(),
        }
        artifact = _record_agentledger_artifact(
            settings,
            payload,
            name=f"draft_{template_result['document_type']}.json",
            kind="draft_document",
            value={
                "run_id": payload["run_id"],
                "agentledger_run_id": payload["agentledger_run_id"],
                "document_type": template_result["document_type"],
                "template_result": template_result,
                "generation_mode": draft["generation_mode"],
                "llm_generation": llm_result.to_audit_payload(),
                "facts": facts,
                "evidence_pack": evidence_pack,
                "case_search_result": payload.get("case_search_result"),
                "amount_calculation": amount_calculation,
                "draft": draft,
            },
            metadata={
                "node": "DRAFT",
                "document_type": template_result["document_type"],
                "generation_mode": draft["generation_mode"],
                "llm_enabled": llm_result.enabled,
            },
        )
        draft["agentledger_draft_artifact_id"] = artifact["artifact_id"]
        draft["agentledger_draft_blob_ref"] = artifact["blob_ref"]
        patch_agentledger_state(settings, payload["agentledger_run_id"], {"draft": draft}, "draft activity")
        repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.REVIEW, current_node_status=NodeStatus.RUNNING)
        return {**payload, "draft": draft}

    llm_result = await generate_labor_arbitration_markdown_result(
        settings,
        user_input=user_input,
        facts=facts,
        evidence_pack=evidence_pack,
        amount_calculation=amount_calculation,
        template_markdown=str(template_result.get("markdown") or ""),
        metadata={**_metadata(payload), "node": "DRAFT", "task_type": task_type},
    )
    llm_markdown = llm_result.markdown
    markdown = llm_markdown or str(template_result.get("markdown") or "")
    draft = {
        "template_id": template_result["template_id"],
        "document_type": template_result["document_type"],
        "title": template_result["title"],
        "format": template_result["format"],
        "available_formats": ["markdown", "docx"],
        "generation_mode": "llm" if llm_markdown else "template",
        "llm_provider": settings.llm_provider if llm_markdown else None,
        "llm_model": settings.llm_model if llm_markdown else None,
        "markdown": markdown,
        "applicant": {"name": facts.get("applicant_name", "待补充")},
        "respondent": {"name": facts.get("company_name", "待补充")},
        "claims": str(fields["claims_md"]).splitlines(),
        "facts_and_reasons": str(fields["facts_and_reasons_md"]).splitlines(),
        "legal_basis": str(fields["legal_basis_md"]).splitlines(),
        "evidence_list": str(fields["evidence_list_md"]).splitlines(),
        "pending_fields": list(template_result.get("pending_fields") or []),
        "risk_notice": ["本结果为法律文书草稿，正式提交前需要人工复核。"],
    }
    artifact = _record_agentledger_artifact(
        settings,
        payload,
        name=f"draft_{template_result['document_type']}.json",
        kind="draft_document",
        value={
            "run_id": payload["run_id"],
            "agentledger_run_id": payload["agentledger_run_id"],
            "document_type": template_result["document_type"],
            "template_result": template_result,
            "generation_mode": draft["generation_mode"],
            "llm_generation": llm_result.to_audit_payload(),
            "facts": facts,
            "evidence_pack": evidence_pack,
            "case_search_result": payload.get("case_search_result"),
            "amount_calculation": amount_calculation,
            "draft": draft,
        },
        metadata={
            "node": "DRAFT",
            "document_type": template_result["document_type"],
            "generation_mode": draft["generation_mode"],
            "llm_enabled": llm_result.enabled,
        },
    )
    draft["agentledger_draft_artifact_id"] = artifact["artifact_id"]
    draft["agentledger_draft_blob_ref"] = artifact["blob_ref"]
    patch_agentledger_state(settings, payload["agentledger_run_id"], {"draft": draft}, "draft activity")
    repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.REVIEW, current_node_status=NodeStatus.RUNNING)
    return {**payload, "draft": draft}


@activity.defn
async def review_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    draft = dict(payload.get("draft") or {})
    document_type = str(draft.get("document_type") or "")
    required_sections = _required_sections_for_document(document_type, str(payload.get("task_type") or ""))
    with trace_span(settings, "tool.format_checker", {**_metadata(payload), "node": "REVIEW", "document_type": document_type}):
        format_result = await call_format_checker_tool(
            settings,
            agentledger_run_id=payload["agentledger_run_id"],
            args={
                "document_type": document_type or str(payload.get("task_type") or "unknown"),
                "markdown": str(draft.get("markdown") or ""),
                "required_sections": required_sections,
                "_logical_operation": f"format_checker:{document_type or payload.get('task_type') or 'unknown'}:v1",
            },
        )
    with trace_span(settings, "tool.citation_checker", {**_metadata(payload), "node": "REVIEW", "document_type": document_type}):
        citation_result = await call_citation_checker_tool(
            settings,
            agentledger_run_id=payload["agentledger_run_id"],
            args={
                "document_type": document_type or str(payload.get("task_type") or "unknown"),
                "legal_basis": list(draft.get("legal_basis") or []),
                "evidence_pack": list(payload.get("evidence_pack") or []),
            },
        )
    if draft.get("document_type") == "labor_dispute_case_analysis" or payload.get("task_type") == "case_analysis":
        review = _review_case_analysis(
            draft,
            list(payload.get("evidence_pack") or []),
            list(payload.get("missing_fields") or []),
        )
    else:
        review = _review_draft(
            draft,
            list(payload.get("evidence_pack") or []),
            list(payload.get("missing_fields") or []),
        )
    review = _merge_format_checker_result(review, format_result)
    review = _merge_citation_checker_result(review, citation_result)
    artifact = _record_agentledger_artifact(
        settings,
        payload,
        name="review_result.json",
        kind="review_result",
        value={
            "run_id": payload["run_id"],
            "agentledger_run_id": payload["agentledger_run_id"],
            "document_type": draft.get("document_type"),
            "review_result": review,
            "format_checker_result": format_result,
            "citation_checker_result": citation_result,
            "draft_artifact_id": draft.get("agentledger_draft_artifact_id"),
            "evidence_count": len(list(payload.get("evidence_pack") or [])),
        },
        metadata={
            "node": "REVIEW",
            "document_type": draft.get("document_type"),
            "passed": bool(review.get("passed")),
        },
    )
    review["agentledger_review_artifact_id"] = artifact["artifact_id"]
    review["agentledger_review_blob_ref"] = artifact["blob_ref"]
    patch_agentledger_state(settings, payload["agentledger_run_id"], {"review_result": review}, "review activity")
    repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.APPROVAL, current_node_status=NodeStatus.RUNNING)
    return {**payload, "review_result": review}


@activity.defn
async def approval_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    row = repo.get_run(payload["run_id"]) or {}
    review_result = dict(payload.get("review_result") or {})
    draft = dict(payload.get("draft") or {})
    if not _requires_human_approval(row, review_result):
        approval = {"status": "NOT_REQUIRED", "reason": "output options do not require human review"}
        patch_agentledger_state(settings, payload["agentledger_run_id"], {"approval": approval}, "approval activity")
        repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.OUTPUT, current_node_status=NodeStatus.RUNNING)
        return {**payload, "can_continue": True, "approval": approval}

    is_case_analysis = draft.get("document_type") == "labor_dispute_case_analysis" or payload.get("task_type") == "case_analysis"
    reason = "正式输出法律分析报告草稿前需要人工复核。" if is_case_analysis else "正式输出法律文书草稿前需要人工复核。"
    approval_key = f"{payload['agentledger_run_id']}:final_document_output:v1"
    request = {
        "run_id": payload["run_id"],
        "agentledger_run_id": payload["agentledger_run_id"],
        "approval_key": approval_key,
        "document_type": draft.get("document_type"),
        "title": draft.get("title"),
        "review_result": review_result,
        "pending_fields": draft.get("pending_fields") or [],
        "risk_notice": draft.get("risk_notice") or [],
    }
    agentledger_approval = request_agentledger_approval(
        settings,
        agentledger_run_id=payload["agentledger_run_id"],
        approval_key=approval_key,
        request=request,
        tool_name="legal_agent.output_case_analysis" if is_case_analysis else "legal_agent.output_document",
        risk_level=str(row.get("risk_level") or "L2"),
        reason=reason,
    )
    approval_id = str(agentledger_approval["approval_id"])
    approval_row = repo.upsert_approval_request(
        approval_id=approval_id,
        run_id=payload["run_id"],
        agentledger_run_id=payload["agentledger_run_id"],
        agentledger_approval_id=approval_id,
        approval_key=approval_key,
        status="PENDING",
        risk_level=str(row.get("risk_level") or "L2"),
        reason=reason,
        request_json=request,
        review_result_json=review_result,
        document_json=draft,
        requested_by="legal-agent",
    )
    approval = {
        "status": "PENDING",
        "approval_id": approval_id,
        "agentledger_approval_id": approval_id,
        "approval_key": approval_key,
        "reason": reason,
    }
    patch_agentledger_state(settings, payload["agentledger_run_id"], {"approval": approval}, "approval activity")
    repo.update_status(payload["run_id"], run_status=RunStatus.WAITING_APPROVAL, current_node=NodeName.APPROVAL, current_node_status=NodeStatus.WAITING)
    return {
        **payload,
        "can_continue": False,
        "approval": approval,
        "approval_id": approval_id,
        "approval_request": approval_row,
    }


@activity.defn
async def approval_decision_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    signal = dict(payload.get("approval_signal") or {})
    approval_id = str(signal.get("approval_id") or payload.get("approval_id") or "")
    approved = bool(signal.get("approved"))
    approver = str(signal.get("approver") or "demo-reviewer")
    reason = str(signal.get("reason") or "")
    approval_row = repo.get_approval_request(payload["run_id"], approval_id)
    if approval_row is None:
        raise KeyError(f"approval not found: {approval_id}")
    decide_agentledger_approval(
        settings,
        agentledger_approval_id=str(approval_row["agentledger_approval_id"]),
        approved=approved,
        approver=approver,
        reason=reason,
    )
    decided = repo.decide_approval_request(payload["run_id"], approval_id, approved=approved, approver=approver, reason=reason)
    approval = {
        "status": decided["status"],
        "approval_id": approval_id,
        "agentledger_approval_id": approval_row["agentledger_approval_id"],
        "approver": approver,
        "reason": reason,
    }
    patch_agentledger_state(settings, payload["agentledger_run_id"], {"approval": approval}, "approval decision activity")
    _record_agentledger_artifact(
        settings,
        payload,
        name="approval_decision.json",
        kind="approval_decision",
        value={
            "run_id": payload["run_id"],
            "agentledger_run_id": payload["agentledger_run_id"],
            "approval": approval,
            "approval_signal": signal,
            "review_result": payload.get("review_result") or {},
        },
        metadata={
            "node": "APPROVAL",
            "approval_status": approval["status"],
            "approval_id": approval_id,
        },
    )
    if not approved:
        result = {"approval": approval, "review_result": payload.get("review_result") or {}}
        repo.update_status(
            payload["run_id"],
            run_status=RunStatus.APPROVAL_REJECTED,
            current_node=NodeName.APPROVAL,
            current_node_status=NodeStatus.FAILED,
            result_summary=result,
        )
        return {**payload, "can_continue": False, "approval": approval, "result": result}
    repo.update_status(payload["run_id"], run_status=RunStatus.RUNNING, current_node=NodeName.OUTPUT, current_node_status=NodeStatus.RUNNING)
    return {**payload, "can_continue": True, "approval": approval}


@activity.defn
async def approval_timeout_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    approval_id = str(payload.get("approval_id") or "")
    if not approval_id:
        raise KeyError("approval_id")
    approval_row = repo.get_approval_request(payload["run_id"], approval_id)
    if approval_row is None:
        raise KeyError(f"approval not found: {approval_id}")
    agentledger_decision_error: str | None = None
    if approval_row.get("status") == "PENDING":
        try:
            decide_agentledger_approval(
                settings,
                agentledger_approval_id=str(approval_row["agentledger_approval_id"]),
                approved=False,
                approver="system-timeout",
                reason="approval_timeout",
            )
        except Exception as exc:
            agentledger_decision_error = repr(exc)
    expired = repo.expire_approval_request(payload["run_id"], approval_id, reason="approval_timeout")
    approval = {
        "status": "EXPIRED",
        "approval_id": approval_id,
        "agentledger_approval_id": approval_row["agentledger_approval_id"],
        "approval_key": approval_row["approval_key"],
        "reason": "approval_timeout",
        "timeout_seconds": payload.get("approval_timeout_seconds"),
    }
    result = {
        "approval": approval,
        "review_result": payload.get("review_result") or {},
        "expire_reason": "approval_timeout",
    }
    patch_agentledger_state(
        settings,
        payload["agentledger_run_id"],
        {"approval": approval, "result": result, "expire_reason": "approval_timeout"},
        "approval timeout activity",
    )
    _record_agentledger_artifact(
        settings,
        payload,
        name="approval_timeout.json",
        kind="approval_timeout",
        value={
            "run_id": payload["run_id"],
            "agentledger_run_id": payload["agentledger_run_id"],
            "approval": approval,
            "approval_request": {
                "approval_id": expired.get("approval_id"),
                "status": expired.get("status"),
                "decision_reason": expired.get("decision_reason"),
                "decided_by": expired.get("decided_by"),
                "decided_at": str(expired.get("decided_at")) if expired.get("decided_at") else None,
            },
            "agentledger_decision_error": agentledger_decision_error,
        },
        metadata={
            "node": "APPROVAL",
            "approval_status": "EXPIRED",
            "approval_id": approval_id,
            "expire_reason": "approval_timeout",
        },
    )
    repo.update_status(
        payload["run_id"],
        run_status=RunStatus.EXPIRED,
        current_node=NodeName.APPROVAL,
        current_node_status=NodeStatus.FAILED,
        result_summary=result,
        last_error="approval_timeout",
    )
    return {**payload, "can_continue": False, "approval": approval, "result": result}


@activity.defn
async def output_activity(payload: dict[str, Any]) -> dict[str, Any]:
    settings = load_settings()
    repo = RunRepository(settings)
    row = repo.get_run(payload["run_id"]) or {}
    facts = dict(row.get("facts_json") or {})
    document = payload.get("draft", {})
    review_result = payload.get("review_result", {})
    document_id = f"doc_{payload['run_id'].removeprefix('run_')}"
    markdown = str(document.get("markdown") or "")
    markdown_path = _write_markdown_artifact(settings, payload["run_id"], document_id, markdown)
    docx_path = _write_docx_artifact(settings, payload["run_id"], document_id, markdown)
    artifact_payload = {
        "document_id": document_id,
        "run_id": payload["run_id"],
        "document": document,
        "review_result": review_result,
        "markdown_path": markdown_path,
        "docx_path": docx_path,
    }
    artifact = create_agentledger_artifact(
        settings,
        agentledger_run_id=payload["agentledger_run_id"],
        name=f"{document_id}.json",
        value=artifact_payload,
        metadata={
            "kind": "generated_legal_document",
            "document_type": document.get("document_type"),
            "format": document.get("format"),
            "markdown_path": markdown_path,
            "docx_path": docx_path,
        },
    )
    repo.upsert_generated_document(
        run_id=payload["run_id"],
        document_id=document_id,
        document_type=str(document.get("document_type") or "unknown"),
        jurisdiction=payload.get("jurisdiction") or row.get("jurisdiction") or "CN",
        title=str(document.get("title") or "法律文书"),
        status="DRAFT",
        document_json=document,
        markdown=markdown,
        markdown_path=markdown_path,
        docx_path=docx_path,
        facts_json=facts,
        claims_json=list(document.get("claims") or []),
        legal_basis_json=list(document.get("legal_basis") or []),
        evidence_list_json=list(document.get("evidence_list") or []),
        amount_calculation_json=dict(payload.get("amount_calculation") or _amount_calculation(facts)),
        risk_notice_json=list(document.get("risk_notice") or []),
        review_result_json=review_result,
        agentledger_artifact_id=artifact["artifact_id"],
        agentledger_blob_ref=artifact["blob_ref"],
    )
    result = {
        "document_id": document_id,
        "document": {
            **document,
            "document_id": document_id,
            "markdown_path": markdown_path,
            "docx_path": docx_path,
            "agentledger_artifact_id": artifact["artifact_id"],
            "agentledger_blob_ref": artifact["blob_ref"],
        },
        "review_result": review_result,
        "approval": payload.get("approval") or {"status": "NOT_REQUIRED"},
        "amount_calculation": dict(payload.get("amount_calculation") or _amount_calculation(facts)),
    }
    patch_agentledger_state(settings, payload["agentledger_run_id"], {"result": result}, "output activity")
    repo.update_status(
        payload["run_id"],
        run_status=RunStatus.COMPLETED,
        current_node=NodeName.OUTPUT,
        current_node_status=NodeStatus.SUCCEEDED,
        result_summary=result,
    )
    return {**payload, "result": result}


def _write_markdown_artifact(settings: Any, run_id: str, document_id: str, markdown: str) -> str:
    rel_path = f"generated-documents/{run_id}/{document_id}.md"
    path = settings.data_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    sidecar = path.with_suffix(".json")
    sidecar.write_text(
        json.dumps({"run_id": run_id, "document_id": document_id, "markdown_path": str(path)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def _write_docx_artifact(settings: Any, run_id: str, document_id: str, markdown: str) -> str:
    path = settings.data_dir / "generated-documents" / run_id / f"{document_id}.docx"
    write_markdown_docx(markdown, path)
    return str(path)


def _amount_calculation(facts: dict[str, Any]) -> dict[str, Any]:
    try:
        monthly_salary = float(facts.get("monthly_salary"))
        unpaid_months = int(facts.get("unpaid_months"))
    except (TypeError, ValueError):
        return {"status": "pending"}
    return {
        "status": "calculated",
        "monthly_salary": monthly_salary,
        "unpaid_months": unpaid_months,
        "unpaid_salary_amount": monthly_salary * unpaid_months,
    }
