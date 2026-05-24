from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from legal_agent.core.config import Settings
from legal_agent.core.facts import infer_facts_from_input, missing_fact_fields
from legal_agent.db.repository import RunRepository
from legal_agent.document_templates.case_analysis import render_labor_dispute_case_analysis
from legal_agent.document_templates.labor_arbitration import render_labor_arbitration_application
from legal_agent.tools.citation_checker import citation_checker
from legal_agent.tools.format_checker import format_checker
from legal_agent.tools.salary_calculator import salary_calculator


LEGAL_SOURCE_TYPES = {"law", "judicial_interpretation", "administrative_regulation", "department_rule", "regulation"}
CASE_SOURCE_TYPES = {"case", "typical_case", "guiding_case"}


@dataclass(frozen=True)
class OfflineEvalOptions:
    dataset_path: Path
    output_path: Path | None = None
    fail_on_gate: bool = True


def run_offline_evaluation(settings: Settings, options: OfflineEvalOptions) -> dict[str, Any]:
    dataset = _load_json(options.dataset_path)
    cases = list(dataset.get("cases") or [])
    case_results = [_evaluate_case(settings, case) for case in cases]
    metrics = _metrics(case_results)
    thresholds = dict(dataset.get("acceptance_thresholds") or {})
    gates = _gates(metrics, thresholds)
    report = {
        "dataset_id": dataset.get("dataset_id") or options.dataset_path.stem,
        "case_count": len(case_results),
        "metrics": metrics,
        "thresholds": thresholds,
        "gates": gates,
        "passed": all(gate["passed"] for gate in gates.values()),
        "cases": case_results,
    }
    if options.output_path:
        options.output_path.parent.mkdir(parents=True, exist_ok=True)
        options.output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    if options.fail_on_gate and not report["passed"]:
        failed = ", ".join(name for name, gate in gates.items() if not gate["passed"])
        raise SystemExit(f"offline evaluation gates failed: {failed}")
    return report


def _evaluate_case(settings: Settings, case: dict[str, Any]) -> dict[str, Any]:
    category = str(case.get("category") or "")
    task_type = str(case.get("task_type") or "document_generation")
    expected = dict(case.get("expected") or {})
    input_data = dict(case.get("input") or {})
    text = str(input_data.get("text") or "")
    facts = infer_facts_from_input(text, [])
    facts.update(dict(input_data.get("confirmed_facts") or {}))
    missing = missing_fact_fields(facts, task_type)
    checks: dict[str, bool] = {}
    details: dict[str, Any] = {
        "inferred_facts": facts,
        "missing_fields": missing,
    }

    checks["task_classification"] = _expected_task_type(category) == expected.get("task_type", task_type)
    if "missing_fields" in expected:
        checks["missing_fact_identification"] = sorted(missing) == sorted(expected.get("missing_fields") or [])

    if task_type == "case_search":
        case_rows = _case_search(settings, input_data, case.get("jurisdiction") or "CN")
        found_case_ids = _case_ids(case_rows)
        details["case_ids"] = found_case_ids
        checks["case_retrieval"] = _matches_any(found_case_ids, expected.get("case_ids_any") or [])
        return _case_result(case, checks, details)

    evidence_pack = _evidence_pack(settings, facts, task_type, str(case.get("jurisdiction") or "CN-BJ"))
    details["evidence_count"] = len(evidence_pack)
    details["citation_anchors"] = [item.get("citation_anchor") for item in evidence_pack]
    expected_citations = list(expected.get("legal_citations_any") or [])
    if expected_citations:
        checks["law_recall"] = _matches_any([str(item.get("citation_anchor")) for item in evidence_pack], expected_citations)

    amount_calculation = _amount_calculation(facts, evidence_pack, str(case.get("jurisdiction") or "CN-BJ"))
    details["amount_calculation"] = amount_calculation
    if expected.get("amounts"):
        details["expected_amounts"] = expected["amounts"]
        checks["amount_calculation"] = _amounts_match(amount_calculation, dict(expected["amounts"]))

    document_result = _render_document(task_type, facts, evidence_pack, amount_calculation)
    markdown = document_result["markdown"]
    details["document_type"] = document_result["document_type"]
    details["pending_fields"] = document_result.get("pending_fields") or []
    if expected.get("required_sections"):
        format_result = format_checker(
            {
                "document_type": document_result["document_type"],
                "markdown": markdown,
                "required_sections": expected["required_sections"],
            }
        )
        details["format_checker"] = format_result
        checks["document_structure"] = bool(format_result.get("passed"))

    legal_basis = _legal_basis_from_markdown(markdown)
    citation_result = citation_checker(
        {
            "document_type": document_result["document_type"],
            "legal_basis": legal_basis,
            "evidence_pack": evidence_pack,
        }
    )
    details["citation_checker"] = citation_result
    checks["citation_accuracy"] = bool(citation_result.get("passed"))
    checks["tool_success"] = (
        amount_calculation.get("status") in {"calculated", "requires_more_facts"}
        and document_result.get("status") == "rendered"
        and citation_result.get("status") in {"passed", "failed"}
    )

    if expected.get("case_ids_any"):
        found_case_ids = _case_ids([item for item in evidence_pack if item.get("source_type") in CASE_SOURCE_TYPES])
        details["case_ids"] = found_case_ids
        checks["case_retrieval"] = _matches_any(found_case_ids, expected.get("case_ids_any") or [])
    if expected.get("must_not_complete_document"):
        checks["refusal_or_safe_incomplete"] = bool(missing or details["pending_fields"])
    if expected.get("forbidden_terms"):
        checks["prompt_injection_resistance"] = not any(term in markdown for term in expected.get("forbidden_terms") or [])

    return _case_result(case, checks, details)


def _expected_task_type(category: str) -> str:
    if category == "case_search":
        return "case_search"
    if category == "case_analysis":
        return "case_analysis"
    return "document_generation"


def _evidence_pack(settings: Settings, facts: dict[str, Any], task_type: str, jurisdiction: str) -> list[dict[str, Any]]:
    repo = RunRepository(settings)
    queries = ["劳动争议 仲裁 劳动报酬", "劳动人事争议仲裁申请书"]
    if task_type == "case_analysis":
        queries.append("劳动争议 案情分析 类案 争议焦点")
    if facts.get("contract_signed") is False:
        queries.append("未签 书面劳动合同")
    if facts.get("unpaid_months"):
        queries.append("拖欠 工资 劳动报酬")
    if facts.get("termination_reason"):
        queries.append("解除 劳动合同 赔偿")
    seen: set[str] = set()
    pack: list[dict[str, Any]] = []
    for query in queries:
        for chunk in repo.search_legal_chunks(query=query, jurisdiction=jurisdiction, limit=6):
            if chunk["chunk_id"] in seen:
                continue
            seen.add(chunk["chunk_id"])
            pack.append(_chunk_to_evidence(chunk, query))
    if task_type == "case_analysis":
        for chunk in repo.search_case_chunks(query="劳动争议 仲裁时效 拖欠工资 未签劳动合同 违法解除", jurisdiction=jurisdiction, limit=3):
            if chunk["chunk_id"] in seen:
                continue
            seen.add(chunk["chunk_id"])
            pack.append(_chunk_to_evidence(chunk, "case_analysis"))
    return pack


def _chunk_to_evidence(chunk: dict[str, Any], query: str) -> dict[str, Any]:
    metadata = dict(chunk.get("metadata_json") or {})
    return {
        "chunk_id": chunk["chunk_id"],
        "source_type": chunk["doc_type"],
        "authority_level": chunk["authority_level"],
        "source_name": chunk["title"],
        "source_url": chunk["source_url"],
        "citation_anchor": chunk["citation_anchor"],
        "quote": chunk["content"],
        "supported_claim": metadata.get("supported_claim") or "该资料已进入本地法律库，具体支持关系需人工复核。",
        "score": float(chunk.get("score") or 0),
        "retrieval_method": chunk.get("retrieval_method") or "offline_eval",
        "metadata": {
            "doc_id": chunk["doc_id"],
            "query": query,
            "metadata": metadata,
        },
    }


def _case_search(settings: Settings, input_data: dict[str, Any], jurisdiction: str) -> list[dict[str, Any]]:
    query = str(input_data.get("text") or " ".join(input_data.get("claims") or []))
    return RunRepository(settings).search_case_chunks(query=query, jurisdiction=jurisdiction, limit=10)


def _amount_calculation(facts: dict[str, Any], evidence_pack: list[dict[str, Any]], jurisdiction: str) -> dict[str, Any]:
    legal_refs = [str(evidence.get("citation_anchor")) for evidence in evidence_pack if evidence.get("source_type") in LEGAL_SOURCE_TYPES]
    return salary_calculator(
        {
            "monthly_salary": facts.get("monthly_salary"),
            "unpaid_months": facts.get("unpaid_months"),
            "work_start_date": facts.get("work_start_date"),
            "work_end_date": facts.get("work_end_date"),
            "contract_signed": facts.get("contract_signed"),
            "termination_reason": facts.get("termination_reason"),
            "jurisdiction": jurisdiction,
            "evidence_refs": ["offline_eval"],
            "legal_evidence_refs": legal_refs,
        }
    )


def _render_document(task_type: str, facts: dict[str, Any], evidence_pack: list[dict[str, Any]], amount_calculation: dict[str, Any]) -> dict[str, Any]:
    if task_type == "case_analysis":
        rendered = render_labor_dispute_case_analysis(facts, evidence_pack=evidence_pack, amount_calculation=amount_calculation)
    else:
        rendered = render_labor_arbitration_application(facts, evidence_pack=evidence_pack, amount_calculation=amount_calculation)
    return {
        "status": "rendered",
        "template_id": rendered.template_id,
        "document_type": rendered.document_type,
        "markdown": rendered.markdown,
        "pending_fields": rendered.pending_fields,
    }


def _legal_basis_from_markdown(markdown: str) -> list[str]:
    lines: list[str] = []
    in_section = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped == "## 法律依据":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped and stripped[0].isdigit():
            lines.append(stripped)
    return lines


def _case_ids(rows: list[dict[str, Any]]) -> list[str]:
    ids = []
    for row in rows:
        metadata = dict(row.get("metadata") or row.get("metadata_json") or {})
        nested = metadata.get("metadata") if isinstance(metadata.get("metadata"), dict) else {}
        ids.append(str(metadata.get("case_id") or nested.get("case_id") or row.get("chunk_id")))
    return ids


def _amounts_match(amount_calculation: dict[str, Any], expected: dict[str, Any]) -> bool:
    amounts = {str(item.get("item")): item.get("amount") for item in amount_calculation.get("items") or []}
    for key, expected_value in expected.items():
        actual = amounts.get(key)
        try:
            if abs(float(actual) - float(expected_value)) > 0.01:
                return False
        except (TypeError, ValueError):
            return False
    return True


def _matches_any(actual: list[str], expected_any: list[str]) -> bool:
    actual_set = set(actual)
    return any(item in actual_set for item in expected_any)


def _case_result(case: dict[str, Any], checks: dict[str, bool], details: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case.get("case_id"),
        "category": case.get("category"),
        "passed": all(checks.values()),
        "checks": checks,
        "details": details,
    }


def _metrics(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task_classification_accuracy": _ratio(case_results, "task_classification"),
        "missing_fact_identification_accuracy": _ratio(case_results, "missing_fact_identification"),
        "law_recall": _ratio(case_results, "law_recall"),
        "citation_accuracy": _ratio(case_results, "citation_accuracy"),
        "document_structure_completeness": _ratio(case_results, "document_structure"),
        "amount_calculation_accuracy": _ratio(case_results, "amount_calculation"),
        "case_retrieval_accuracy": _ratio(case_results, "case_retrieval"),
        "prompt_injection_resistance": _ratio(case_results, "prompt_injection_resistance"),
        "refusal_accuracy": _ratio(case_results, "refusal_or_safe_incomplete"),
        "tool_success_rate": _ratio(case_results, "tool_success"),
        "case_pass_rate": sum(1 for case in case_results if case["passed"]) / len(case_results) if case_results else 0.0,
    }


def _ratio(case_results: list[dict[str, Any]], check_name: str) -> float | None:
    applicable = [case for case in case_results if check_name in case.get("checks", {})]
    if not applicable:
        return None
    return sum(1 for case in applicable if case["checks"][check_name]) / len(applicable)


def _gates(metrics: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, dict[str, Any]]:
    gates: dict[str, dict[str, Any]] = {}
    for name, threshold in thresholds.items():
        value = metrics.get(name)
        gates[name] = {
            "value": value,
            "threshold": threshold,
            "passed": value is not None and float(value) >= float(threshold),
        }
    return gates


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
