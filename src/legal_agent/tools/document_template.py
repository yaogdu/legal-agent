from __future__ import annotations

from typing import Any

from legal_agent.document_templates.case_analysis import render_labor_dispute_case_analysis
from legal_agent.document_templates.labor_arbitration import render_labor_arbitration_application


DOCUMENT_TEMPLATE_INPUT_SCHEMA = {
    "type": "object",
    "required": ["task_type", "facts", "evidence_pack", "amount_calculation"],
    "properties": {
        "task_type": {"type": "string"},
        "facts": {"type": "object"},
        "evidence_pack": {"type": "array", "items": {"type": "object"}},
        "amount_calculation": {"type": "object"},
    },
}

DOCUMENT_TEMPLATE_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "template_id", "document_type", "title", "format", "markdown", "fields", "pending_fields"],
    "properties": {
        "status": {"type": "string"},
        "template_id": {"type": "string"},
        "document_type": {"type": "string"},
        "title": {"type": "string"},
        "format": {"type": "string"},
        "markdown": {"type": "string"},
        "fields": {"type": "object"},
        "pending_fields": {"type": "array", "items": {"type": "string"}},
    },
}


def document_template_tool(args: dict[str, Any]) -> dict[str, Any]:
    task_type = str(args.get("task_type") or "document_generation")
    facts = dict(args.get("facts") or {})
    evidence_pack = list(args.get("evidence_pack") or [])
    amount_calculation = dict(args.get("amount_calculation") or {})
    if task_type == "case_analysis":
        document = render_labor_dispute_case_analysis(
            facts,
            evidence_pack=evidence_pack,
            amount_calculation=amount_calculation,
        )
    else:
        document = render_labor_arbitration_application(
            facts,
            evidence_pack=evidence_pack,
            amount_calculation=amount_calculation,
        )
    return {
        "status": "rendered",
        "template_id": document.template_id,
        "document_type": document.document_type,
        "title": document.title,
        "format": document.format,
        "markdown": document.markdown,
        "fields": document.fields,
        "pending_fields": document.pending_fields,
    }
