from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import yaml
from psycopg.types.json import Jsonb

from legal_agent.core.config import Settings
from legal_agent.core.ids import new_id
from legal_agent.db.connection import connect


def bootstrap_labor_dispute(settings: Settings) -> dict[str, Any]:
    manifest = yaml.safe_load(settings.rag_source_manifest.read_text(encoding="utf-8"))
    seed = json.loads(settings.rag_seed_file.read_text(encoding="utf-8"))
    project_root = Path(__file__).resolve().parents[3]
    chunk_metadata = _load_chunk_metadata(project_root, seed.get("chunk_metadata_path"))
    ingest_id = new_id("ingest")
    snapshot_dir = settings.data_dir / "rag" / "snapshots" / ingest_id
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    stats = {"documents": 0, "chunks": 0, "manifest_sources": len(manifest.get("sources", []))}
    errors: list[dict[str, Any]] = []

    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO rag.rag_ingest_run(ingest_id, domain, source_manifest, status) VALUES (%s,%s,%s,%s)",
            (ingest_id, seed.get("domain", "labor_dispute"), str(settings.rag_source_manifest), "RUNNING"),
        )
        for doc in seed.get("documents", []):
            try:
                source_content = _read_source_content(project_root, doc)
                content_hash = _hash_doc(doc, source_content)
                snapshot_path = _write_source_snapshot(snapshot_dir, doc, source_content)
                conn.execute(
                    """
                    INSERT INTO rag.legal_source_document(
                      source_id, doc_id, doc_type, authority_level, title, source_url, jurisdiction,
                      issuing_authority, document_no, version_hash, status, snapshot_path, metadata_json
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(doc_id) DO UPDATE SET
                      title=EXCLUDED.title,
                      source_url=EXCLUDED.source_url,
                      jurisdiction=EXCLUDED.jurisdiction,
                      issuing_authority=EXCLUDED.issuing_authority,
                      document_no=EXCLUDED.document_no,
                      version_hash=EXCLUDED.version_hash,
                      status=EXCLUDED.status,
                      snapshot_path=EXCLUDED.snapshot_path,
                      metadata_json=EXCLUDED.metadata_json,
                      updated_at=now()
                    """,
                    (
                        doc["source_id"],
                        doc["doc_id"],
                        doc["doc_type"],
                        doc["authority_level"],
                        doc["title"],
                        doc["source_url"],
                        doc["jurisdiction"],
                        doc.get("issuing_authority"),
                        doc.get("document_no"),
                        content_hash,
                        "ACTIVE",
                        str(snapshot_path),
                        Jsonb(
                            {
                                "seed": True,
                                "content_path": doc.get("content_path"),
                                "snapshot_format": snapshot_path.suffix.lstrip(".") or "json",
                            }
                        ),
                    ),
                )
                stats["documents"] += 1
                conn.execute("DELETE FROM rag.legal_document_chunk WHERE doc_id=%s", (doc["doc_id"],))
                chunks = _chunks_for_doc(doc, source_content, chunk_metadata)
                if not chunks:
                    raise ValueError(f"no chunks produced for {doc['doc_id']}")
                for chunk in chunks:
                    if not chunk.get("content"):
                        raise ValueError(f"empty chunk content for {chunk.get('chunk_id')} in {doc['doc_id']}")
                    embedding = _demo_embedding(chunk["content"])
                    conn.execute(
                        """
                        INSERT INTO rag.legal_document_chunk(
                          chunk_id, doc_id, doc_type, authority_level, title, content, metadata_json,
                          citation_anchor, jurisdiction, embedding
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT(chunk_id) DO UPDATE SET
                          content=EXCLUDED.content,
                          metadata_json=EXCLUDED.metadata_json,
                          citation_anchor=EXCLUDED.citation_anchor,
                          embedding=EXCLUDED.embedding
                        """,
                        (
                            chunk["chunk_id"],
                            doc["doc_id"],
                            doc["doc_type"],
                            doc["authority_level"],
                            doc["title"],
                            chunk["content"],
                            Jsonb(
                                {
                                    "source_id": doc["source_id"],
                                    "seed": True,
                                    "content_path": doc.get("content_path"),
                                    **(chunk.get("metadata") or {}),
                                }
                            ),
                            chunk.get("citation_anchor"),
                            doc["jurisdiction"],
                            embedding,
                        ),
                    )
                    stats["chunks"] += 1
            except Exception as exc:
                errors.append({"doc_id": doc.get("doc_id"), "error": repr(exc)})
        status = "SUCCEEDED" if not errors else "FAILED"
        conn.execute(
            "UPDATE rag.rag_ingest_run SET status=%s, stats_json=%s, error_json=%s, finished_at=now() WHERE ingest_id=%s",
            (status, Jsonb(stats), Jsonb(errors), ingest_id),
        )
        conn.commit()
    return {"ingest_id": ingest_id, "status": "SUCCEEDED" if not errors else "FAILED", "stats": stats, "errors": errors}


def backfill_missing_embeddings(settings: Settings, *, limit: int = 100) -> dict[str, Any]:
    limit = max(1, min(limit, 1000))
    with connect(settings) as conn:
        rows = conn.execute(
            """
            SELECT chunk_id, content
            FROM rag.legal_document_chunk
            WHERE embedding IS NULL
            ORDER BY created_at ASC, chunk_id ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE rag.legal_document_chunk SET embedding=%s WHERE chunk_id=%s",
                (_demo_embedding(str(row["content"] or "")), row["chunk_id"]),
            )
        remaining = conn.execute(
            "SELECT count(*)::int AS count FROM rag.legal_document_chunk WHERE embedding IS NULL"
        ).fetchone()
        conn.commit()
    return {
        "status": "ok",
        "processed": len(rows),
        "remaining": int(remaining["count"] if remaining else 0),
        "limit": limit,
    }


def _read_source_content(project_root: Path, doc: dict[str, Any]) -> str | None:
    raw_path = doc.get("content_path")
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.read_text(encoding="utf-8")


def _write_source_snapshot(snapshot_dir: Path, doc: dict[str, Any], source_content: str | None) -> Path:
    if source_content is not None:
        suffix = Path(str(doc.get("content_path") or "")).suffix or ".md"
        snapshot_path = snapshot_dir / f"{doc['doc_id']}{suffix}"
        snapshot_path.write_text(source_content, encoding="utf-8")
        metadata_path = snapshot_dir / f"{doc['doc_id']}.metadata.json"
        metadata_path.write_text(
            json.dumps(_doc_metadata_for_hash(doc), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return snapshot_path
    snapshot_path = snapshot_dir / f"{doc['doc_id']}.json"
    snapshot_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return snapshot_path


def _load_chunk_metadata(project_root: Path, raw_path: str | None) -> dict[str, dict[str, Any]]:
    if not raw_path:
        return {}
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    chunks = data.get("chunks", data)
    if not isinstance(chunks, dict):
        raise ValueError(f"chunk metadata must be a mapping: {path}")
    return {str(anchor): dict(metadata or {}) for anchor, metadata in chunks.items()}


def _chunks_for_doc(doc: dict[str, Any], source_content: str | None, chunk_metadata: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    if doc.get("chunks"):
        chunks = []
        for chunk in doc["chunks"]:
            anchor = str(chunk.get("citation_anchor") or "")
            chunks.append(
                {
                    **chunk,
                    "content": chunk.get("content") or _extract_markdown_section(source_content or "", anchor, chunk_metadata),
                    "metadata": {**(chunk.get("metadata") or {}), **chunk_metadata.get(anchor, {})},
                }
            )
        return chunks
    if source_content:
        return _parse_markdown_chunks(doc["doc_id"], source_content, chunk_metadata)
    return []


def _parse_markdown_chunks(doc_id: str, content: str, chunk_metadata: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_anchor: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        if current_anchor is None:
            return
        normalized = _normalize_chunk_content("\n".join(current_lines))
        if not normalized:
            return
        sections.append(
            {
                "chunk_id": _chunk_id(doc_id, current_anchor, len(sections) + 1),
                "citation_anchor": current_anchor,
                "content": normalized,
                "metadata": {
                    "source_format": "markdown",
                    "section_index": len(sections) + 1,
                    **chunk_metadata.get(current_anchor, {}),
                },
            }
        )

    for line in content.splitlines():
        if line.startswith("## "):
            flush()
            current_anchor = line[3:].strip()
            current_lines = []
            continue
        if current_anchor is not None:
            current_lines.append(line)
    flush()
    return sections


def _extract_markdown_section(content: str, citation_anchor: str, chunk_metadata: dict[str, dict[str, Any]]) -> str:
    chunks = _parse_markdown_chunks("doc", content, chunk_metadata)
    for chunk in chunks:
        if chunk["citation_anchor"] == citation_anchor:
            return chunk["content"]
    return ""


def _normalize_chunk_content(content: str) -> str:
    lines = [line.strip() for line in content.splitlines()]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        blank = not line
        if blank and previous_blank:
            continue
        compact.append(line)
        previous_blank = blank
    return "\n".join(compact).strip()


def _chunk_id(doc_id: str, citation_anchor: str, index: int) -> str:
    safe_doc_id = re.sub(r"[^A-Za-z0-9]+", "_", doc_id).strip("_")[:36]
    digest = hashlib.sha1(f"{doc_id}:{citation_anchor}:{index}".encode("utf-8")).hexdigest()[:12]
    return f"chunk_{safe_doc_id}_{digest}"


def _hash_doc(doc: dict[str, Any], source_content: str | None) -> str:
    raw = json.dumps(
        {"metadata": _doc_metadata_for_hash(doc), "content": source_content},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _doc_metadata_for_hash(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in doc.items() if key not in {"chunks"}}


def _demo_embedding(text: str, dimensions: int = 1024) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = []
    for index in range(dimensions):
        byte = digest[index % len(digest)]
        values.append(round((byte / 255.0) - 0.5, 6))
    return "[" + ",".join(str(v) for v in values) + "]"
