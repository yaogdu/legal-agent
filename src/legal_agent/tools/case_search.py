from __future__ import annotations

from typing import Any


CASE_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "required": ["query", "jurisdiction", "top_k"],
    "properties": {
        "query": {"type": "string"},
        "jurisdiction": {"type": "string"},
        "top_k": {"type": "integer"},
        "_logical_operation": {"type": "string"},
    },
}

CASE_SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "query", "jurisdiction", "cases"],
    "properties": {
        "status": {"type": "string"},
        "query": {"type": "string"},
        "jurisdiction": {"type": "string"},
        "cases": {"type": "array", "items": {"type": "object"}},
    },
}


def case_from_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("metadata_json") or {})
    return {
        "case_id": metadata.get("case_id") or row["chunk_id"],
        "title": metadata.get("case_title") or row["title"],
        "court": metadata.get("court") or row.get("issuing_authority"),
        "summary": metadata.get("summary") or row["content"],
        "issue": metadata.get("issue"),
        "holding": metadata.get("holding"),
        "result": metadata.get("result"),
        "source_url": row.get("source_url"),
        "citation_anchor": row.get("citation_anchor"),
        "authority_level": row.get("authority_level"),
        "score": float(row.get("score") or 0),
    }
