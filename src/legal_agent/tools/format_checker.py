from __future__ import annotations

from typing import Any


FORMAT_CHECKER_INPUT_SCHEMA = {
    "type": "object",
    "required": ["document_type", "markdown", "required_sections"],
    "properties": {
        "document_type": {"type": "string"},
        "markdown": {"type": "string"},
        "required_sections": {"type": "array", "items": {"type": "string"}},
        "_logical_operation": {"type": "string"},
    },
}

FORMAT_CHECKER_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "passed", "missing_sections", "present_sections", "notes"],
    "properties": {
        "status": {"type": "string"},
        "passed": {"type": "boolean"},
        "missing_sections": {"type": "array", "items": {"type": "string"}},
        "present_sections": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def format_checker(args: dict[str, Any]) -> dict[str, Any]:
    markdown = str(args.get("markdown") or "")
    required_sections = [str(section) for section in args.get("required_sections") or []]
    present_sections = [section for section in required_sections if section in markdown]
    missing_sections = [section for section in required_sections if section not in markdown]
    passed = not missing_sections
    notes = ["文书结构栏目完整。"] if passed else ["文书缺少必需栏目，需要补齐后再输出。"]
    return {
        "status": "passed" if passed else "failed",
        "passed": passed,
        "missing_sections": missing_sections,
        "present_sections": present_sections,
        "notes": notes,
    }
