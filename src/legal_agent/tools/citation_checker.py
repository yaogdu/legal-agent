from __future__ import annotations

from typing import Any


LEGAL_SOURCE_TYPES = {"law", "judicial_interpretation", "administrative_regulation", "department_rule", "regulation"}

CITATION_CHECKER_INPUT_SCHEMA = {
    "type": "object",
    "required": ["document_type", "legal_basis", "evidence_pack"],
    "properties": {
        "document_type": {"type": "string"},
        "legal_basis": {"type": "array", "items": {"type": "string"}},
        "evidence_pack": {"type": "array", "items": {"type": "object"}},
    },
}

CITATION_CHECKER_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "passed", "missing_citations", "legal_evidence_count"],
    "properties": {
        "status": {"type": "string"},
        "passed": {"type": "boolean"},
        "missing_citations": {"type": "array", "items": {"type": "string"}},
        "legal_evidence_count": {"type": "integer"},
        "checked_citations": {"type": "array", "items": {"type": "string"}},
        "available_citations": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def citation_checker(args: dict[str, Any]) -> dict[str, Any]:
    evidence_pack = list(args.get("evidence_pack") or [])
    legal_basis = [str(line) for line in args.get("legal_basis") or []]
    legal_evidence_anchors = {
        str(evidence.get("citation_anchor"))
        for evidence in evidence_pack
        if evidence.get("source_type") in LEGAL_SOURCE_TYPES and evidence.get("citation_anchor")
    }
    checked = [_citation_anchor(line) for line in legal_basis]
    checked = [anchor for anchor in checked if anchor]
    missing = [anchor for anchor in checked if anchor not in legal_evidence_anchors]
    legal_evidence_count = len(legal_evidence_anchors)
    passed = legal_evidence_count > 0 and bool(checked) and not missing
    notes = []
    if legal_evidence_count == 0:
        notes.append("未找到可作为法律依据的检索证据。")
    if not checked:
        notes.append("文书未列出可校验的法律依据。")
    if missing:
        notes.append("部分法律依据未出现在 evidence_pack 中。")
    if not notes:
        notes.append("法律依据均可回溯到 evidence_pack。")
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "missing_citations": missing,
        "legal_evidence_count": legal_evidence_count,
        "checked_citations": checked,
        "available_citations": sorted(legal_evidence_anchors),
        "notes": notes,
    }


def _citation_anchor(line: str) -> str:
    raw = line.split("：", 1)[0].strip()
    return raw.split(". ", 1)[1].strip() if ". " in raw else raw
