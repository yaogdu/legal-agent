from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from legal_agent.core.enums import NodeName, NodeStatus, RunStatus

from .connection import connect
from legal_agent.core.config import Settings


class RunRepository:
    def __init__(self, settings: Settings):
        self.settings = settings

    def begin_idempotent_request(self, *, scope: str, idempotency_key: str, request_hash: str) -> dict[str, Any]:
        with connect(self.settings) as conn:
            inserted = conn.execute(
                """
                INSERT INTO legal_agent.api_idempotency_key(scope, idempotency_key, request_hash, state)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT(scope, idempotency_key) DO NOTHING
                RETURNING *
                """,
                (scope, idempotency_key, request_hash, "IN_PROGRESS"),
            ).fetchone()
            if inserted is not None:
                conn.commit()
                return {"status": "started", "row": dict(inserted)}
            row = conn.execute(
                """
                SELECT *
                FROM legal_agent.api_idempotency_key
                WHERE scope=%s AND idempotency_key=%s
                """,
                (scope, idempotency_key),
            ).fetchone()
            if row is None:
                raise KeyError(idempotency_key)
            conn.commit()
            if row["request_hash"] != request_hash:
                return {"status": "conflict", "row": dict(row)}
            if row["state"] == "SUCCEEDED" and row.get("response_json") is not None:
                return {"status": "replay", "row": dict(row), "response_json": row["response_json"]}
            return {"status": "in_progress", "row": dict(row)}

    def complete_idempotent_request(
        self,
        *,
        scope: str,
        idempotency_key: str,
        request_hash: str,
        response_json: dict[str, Any],
        status_code: int = 200,
    ) -> None:
        with connect(self.settings) as conn:
            conn.execute(
                """
                UPDATE legal_agent.api_idempotency_key
                   SET state='SUCCEEDED',
                       response_json=%s,
                       status_code=%s,
                       updated_at=now()
                 WHERE scope=%s
                   AND idempotency_key=%s
                   AND request_hash=%s
                """,
                (Jsonb(response_json), status_code, scope, idempotency_key, request_hash),
            )
            conn.commit()

    def create_run(
        self,
        *,
        run_id: str,
        agentledger_run_id: str,
        request_id: str,
        tenant_id: str,
        user_id: str,
        task_type: str,
        legal_domain: str,
        jurisdiction: str,
        risk_level: str,
        run_status: RunStatus,
        current_node: NodeName,
        current_node_status: NodeStatus,
        input_json: dict[str, Any],
        missing_fields: list[str],
        temporal_workflow_id: str | None = None,
    ) -> dict[str, Any]:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                INSERT INTO legal_agent.legal_agent_run(
                  run_id, agentledger_run_id, request_id, temporal_workflow_id, tenant_id, user_id,
                  task_type, legal_domain, jurisdiction, risk_level, run_status, current_node,
                  current_node_status, input_json, missing_fields_json
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    run_id,
                    agentledger_run_id,
                    request_id,
                    temporal_workflow_id,
                    tenant_id,
                    user_id,
                    task_type,
                    legal_domain,
                    jurisdiction,
                    risk_level,
                    run_status.value,
                    current_node.value,
                    current_node_status.value,
                    Jsonb(input_json),
                    Jsonb(missing_fields),
                ),
            ).fetchone()
            conn.commit()
            return dict(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with connect(self.settings) as conn:
            row = conn.execute("SELECT * FROM legal_agent.legal_agent_run WHERE run_id=%s", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                SELECT run_id, agentledger_run_id, request_id, temporal_workflow_id,
                       task_type, legal_domain, jurisdiction, risk_level,
                       run_status, current_node, current_node_status,
                       missing_fields_json, result_summary_json,
                       created_at, updated_at
                FROM legal_agent.legal_agent_run
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_run_audit(self, run_id: str) -> dict[str, Any] | None:
        schema = sql.Identifier(self.settings.agentledger_postgres_schema)
        with connect(self.settings) as conn:
            business_run = conn.execute("SELECT * FROM legal_agent.legal_agent_run WHERE run_id=%s", (run_id,)).fetchone()
            if business_run is None:
                return None
            agentledger_run_id = str(business_run["agentledger_run_id"])
            agentledger_run = conn.execute(
                sql.SQL(
                    """
                    SELECT run_id, session_id, status, state_json, state_version, created_at, updated_at
                    FROM {}.runs
                    WHERE run_id=%s
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchone()
            steps = conn.execute(
                sql.SQL(
                    """
                    SELECT step_id, run_id, session_id, status, owner, lease_token, lease_until,
                           attempt, state_version, checkpoint_id, next_wake_condition,
                           last_error_type, last_error, created_at, updated_at,
                           last_heartbeat_at, cancelled_at
                    FROM {}.steps
                    WHERE run_id=%s
                    ORDER BY created_at ASC, step_id ASC
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchall()
            events = conn.execute(
                sql.SQL(
                    """
                    SELECT event_id, run_id, session_id, step_id, seq, type, timestamp,
                           agent_role, state_version, causal_token, payload_hash, payload_ref
                    FROM {}.events
                    WHERE run_id=%s
                    ORDER BY seq ASC
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchall()
            tool_ledger = conn.execute(
                sql.SQL(
                    """
                    SELECT ledger_id, run_id, session_id, step_id, tool_name, tool_version,
                           tool_call_id, idempotency_key, causal_token, request_hash,
                           request_ref, status, external_id, response_hash, response_ref,
                           error_type, created_at, updated_at
                    FROM {}.tool_ledger
                    WHERE run_id=%s
                    ORDER BY created_at ASC, ledger_id ASC
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchall()
            artifacts = conn.execute(
                sql.SQL(
                    """
                    SELECT artifact_id, run_id, step_id, name, blob_hash, blob_ref,
                           metadata_json, created_at
                    FROM {}.artifacts
                    WHERE run_id=%s
                    ORDER BY created_at ASC, artifact_id ASC
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchall()
            approvals = conn.execute(
                sql.SQL(
                    """
                    SELECT approval_id, approval_key, run_id, session_id, step_id,
                           tool_name, risk_level, status, reason, request_hash,
                           request_ref, requested_by, approved_by, decision_reason,
                           created_at, updated_at
                    FROM {}.approval_requests
                    WHERE run_id=%s
                    ORDER BY created_at ASC, approval_id ASC
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchall()
            cost_records = conn.execute(
                sql.SQL(
                    """
                    SELECT cost_id, run_id, session_id, step_id, category, name,
                           amount, unit, metadata_json, created_at
                    FROM {}.cost_records
                    WHERE run_id=%s
                    ORDER BY created_at ASC, cost_id ASC
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchall()
            evidence_count = conn.execute(
                "SELECT count(*)::int AS count FROM legal_agent.retrieval_evidence WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            document_count = conn.execute(
                "SELECT count(*)::int AS count FROM legal_agent.generated_legal_document WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            documents = conn.execute(
                """
                SELECT document_id, document_type, title, status, markdown_path,
                       docx_path, agentledger_artifact_id, agentledger_blob_ref,
                       created_at, updated_at
                FROM legal_agent.generated_legal_document
                WHERE run_id=%s
                ORDER BY created_at ASC
                """,
                (run_id,),
            ).fetchall()
            event_types = [str(row["type"]) for row in events]
            summary = {
                "event_count": len(events),
                "step_count": len(steps),
                "tool_ledger_count": len(tool_ledger),
                "artifact_count": len(artifacts),
                "approval_count": len(approvals),
                "cost_record_count": len(cost_records),
                "evidence_count": int(evidence_count["count"] if evidence_count else 0),
                "document_count": int(document_count["count"] if document_count else 0),
                "event_types": event_types,
            }
            return {
                "run_id": run_id,
                "agentledger_run_id": agentledger_run_id,
                "business_run": dict(business_run),
                "agentledger_run": dict(agentledger_run) if agentledger_run else None,
                "summary": summary,
                "steps": [dict(row) for row in steps],
                "events": [dict(row) for row in events],
                "tool_ledger": [dict(row) for row in tool_ledger],
                "artifacts": [dict(row) for row in artifacts],
                "approvals": [dict(row) for row in approvals],
                "cost_records": [dict(row) for row in cost_records],
                "documents": [dict(row) for row in documents],
            }

    def get_run_draft(self, run_id: str) -> dict[str, Any] | None:
        schema = sql.Identifier(self.settings.agentledger_postgres_schema)
        with connect(self.settings) as conn:
            business_run = conn.execute(
                """
                SELECT run_id, agentledger_run_id, request_id, temporal_workflow_id,
                       run_status, current_node, current_node_status, updated_at
                FROM legal_agent.legal_agent_run
                WHERE run_id=%s
                """,
                (run_id,),
            ).fetchone()
            if business_run is None:
                return None

            agentledger_run_id = str(business_run["agentledger_run_id"])
            agentledger_run = conn.execute(
                sql.SQL(
                    """
                    SELECT run_id, status, state_json, state_version, updated_at
                    FROM {}.runs
                    WHERE run_id=%s
                    """
                ).format(schema),
                (agentledger_run_id,),
            ).fetchone()
            state_json = _json_object(agentledger_run["state_json"]) if agentledger_run else {}
            approval_request = conn.execute(
                """
                SELECT approval_id, status, risk_level, reason, request_json,
                       review_result_json, document_json, requested_by, decided_by,
                       decision_reason, created_at, updated_at, decided_at
                FROM legal_agent.approval_request
                WHERE run_id=%s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            approval_row = dict(approval_request) if approval_request else None

            draft = _json_object(state_json.get("draft"))
            review_result = _json_object(state_json.get("review_result"))
            approval = _json_object(state_json.get("approval"))
            if approval_row:
                if not draft:
                    draft = _json_object(approval_row.get("document_json"))
                if not review_result:
                    review_result = _json_object(approval_row.get("review_result_json"))
                if not approval:
                    approval = {
                        "approval_id": approval_row.get("approval_id"),
                        "status": approval_row.get("status"),
                        "reason": approval_row.get("reason"),
                    }

            return {
                "run_id": run_id,
                "agentledger_run_id": agentledger_run_id,
                "business_run": dict(business_run),
                "agentledger_run": dict(agentledger_run) if agentledger_run else None,
                "available": bool(draft),
                "draft": draft or None,
                "review_result": review_result or None,
                "approval": approval or None,
                "approval_request": approval_row,
            }

    def collect_metrics(self) -> dict[str, Any]:
        schema_name = self.settings.agentledger_postgres_schema
        schema = sql.Identifier(schema_name)
        with connect(self.settings) as conn:
            business_run_status = _rows(
                conn.execute(
                    """
                    SELECT run_status, task_type, count(*)::int AS count
                    FROM legal_agent.legal_agent_run
                    GROUP BY run_status, task_type
                    ORDER BY run_status, task_type
                    """
                ).fetchall()
            )
            current_node_status = _rows(
                conn.execute(
                    """
                    SELECT COALESCE(current_node, 'UNKNOWN') AS current_node,
                           COALESCE(current_node_status, 'UNKNOWN') AS current_node_status,
                           count(*)::int AS count
                    FROM legal_agent.legal_agent_run
                    GROUP BY current_node, current_node_status
                    ORDER BY current_node, current_node_status
                    """
                ).fetchall()
            )
            approval_status = _rows(
                conn.execute(
                    """
                    SELECT status, risk_level, count(*)::int AS count
                    FROM legal_agent.approval_request
                    GROUP BY status, risk_level
                    ORDER BY status, risk_level
                    """
                ).fetchall()
            )
            idempotency_state = _rows(
                conn.execute(
                    """
                    SELECT state, count(*)::int AS count
                    FROM legal_agent.api_idempotency_key
                    GROUP BY state
                    ORDER BY state
                    """
                ).fetchall()
            )
            business_totals = dict(
                conn.execute(
                    """
                    SELECT
                      (SELECT count(*)::int FROM legal_agent.uploaded_file) AS uploaded_files,
                      (SELECT count(*)::int FROM legal_agent.uploaded_file_chunk) AS uploaded_file_chunks,
                      (SELECT count(*)::int FROM legal_agent.retrieval_evidence) AS retrieval_evidence,
                      (SELECT count(*)::int FROM legal_agent.generated_legal_document) AS generated_documents
                    """
                ).fetchone()
                or {}
            )
            rag_source_documents = _rows(
                conn.execute(
                    """
                    SELECT doc_type, status, count(*)::int AS count
                    FROM rag.legal_source_document
                    GROUP BY doc_type, status
                    ORDER BY doc_type, status
                    """
                ).fetchall()
            )
            rag_chunks = _rows(
                conn.execute(
                    """
                    SELECT doc_type, authority_level, count(*)::int AS count
                    FROM rag.legal_document_chunk
                    GROUP BY doc_type, authority_level
                    ORDER BY doc_type, authority_level
                    """
                ).fetchall()
            )
            latest_ingest = dict(
                conn.execute(
                    """
                    SELECT ingest_id, domain, status, stats_json, started_at, finished_at
                    FROM rag.rag_ingest_run
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                ).fetchone()
                or {}
            )
            agentledger_tables_ready = bool(
                conn.execute(
                    "SELECT to_regclass(%s) IS NOT NULL AS exists",
                    (f"{schema_name}.runs",),
                ).fetchone()["exists"]
            )
            agentledger: dict[str, Any] = {
                "tables_ready": agentledger_tables_ready,
                "runs": [],
                "tool_ledger": [],
                "artifacts": [],
                "approvals": [],
                "cost_records": [],
                "event_count": 0,
            }
            if agentledger_tables_ready:
                agentledger = {
                    "tables_ready": True,
                    "runs": _rows(
                        conn.execute(
                            sql.SQL(
                                """
                                SELECT status, count(*)::int AS count
                                FROM {}.runs
                                GROUP BY status
                                ORDER BY status
                                """
                            ).format(schema)
                        ).fetchall()
                    ),
                    "tool_ledger": _rows(
                        conn.execute(
                            sql.SQL(
                                """
                                SELECT tool_name, status, count(*)::int AS count
                                FROM {}.tool_ledger
                                GROUP BY tool_name, status
                                ORDER BY tool_name, status
                                """
                            ).format(schema)
                        ).fetchall()
                    ),
                    "artifacts": _rows(
                        conn.execute(
                            sql.SQL(
                                """
                                SELECT COALESCE(metadata_json->>'kind', 'unknown') AS kind, count(*)::int AS count
                                FROM {}.artifacts
                                GROUP BY COALESCE(metadata_json->>'kind', 'unknown')
                                ORDER BY kind
                                """
                            ).format(schema)
                        ).fetchall()
                    ),
                    "approvals": _rows(
                        conn.execute(
                            sql.SQL(
                                """
                                SELECT status, risk_level, count(*)::int AS count
                                FROM {}.approval_requests
                                GROUP BY status, risk_level
                                ORDER BY status, risk_level
                                """
                            ).format(schema)
                        ).fetchall()
                    ),
                    "cost_records": _rows(
                        conn.execute(
                            sql.SQL(
                                """
                                SELECT category, name, count(*)::int AS count, COALESCE(sum(amount), 0)::float AS amount
                                FROM {}.cost_records
                                GROUP BY category, name
                                ORDER BY category, name
                                """
                            ).format(schema)
                        ).fetchall()
                    ),
                    "event_count": int(
                        conn.execute(
                            sql.SQL("SELECT count(*)::int AS count FROM {}.events").format(schema)
                        ).fetchone()["count"]
                    ),
                }
            return {
                "business_run_status": business_run_status,
                "current_node_status": current_node_status,
                "approval_status": approval_status,
                "idempotency_state": idempotency_state,
                "business_totals": business_totals,
                "rag_source_documents": rag_source_documents,
                "rag_chunks": rag_chunks,
                "latest_ingest": latest_ingest,
                "agentledger": agentledger,
            }

    def update_status(
        self,
        run_id: str,
        *,
        run_status: RunStatus,
        current_node: NodeName | None = None,
        current_node_status: NodeStatus | None = None,
        missing_fields: list[str] | None = None,
        result_summary: dict[str, Any] | None = None,
        last_error: str | None = None,
    ) -> None:
        assignments = ["run_status=%s", "updated_at=now()"]
        params: list[Any] = [run_status.value]
        if current_node is not None:
            assignments.append("current_node=%s")
            params.append(current_node.value)
        if current_node_status is not None:
            assignments.append("current_node_status=%s")
            params.append(current_node_status.value)
        if missing_fields is not None:
            assignments.append("missing_fields_json=%s")
            params.append(Jsonb(missing_fields))
        if result_summary is not None:
            assignments.append("result_summary_json=%s")
            params.append(Jsonb(result_summary))
        if last_error is not None:
            assignments.append("last_error=%s")
            params.append(last_error)
        if run_status in {
            RunStatus.COMPLETED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
            RunStatus.EXPIRED,
            RunStatus.APPROVAL_REJECTED,
        }:
            assignments.append("completed_at=%s")
            params.append(datetime.now(timezone.utc))
        params.append(run_id)
        with connect(self.settings) as conn:
            conn.execute(f"UPDATE legal_agent.legal_agent_run SET {', '.join(assignments)} WHERE run_id=%s", params)
            conn.commit()

    def merge_facts(self, run_id: str, facts: dict[str, Any], *, source_type: str = "user_input") -> dict[str, Any]:
        with connect(self.settings) as conn:
            existing = conn.execute("SELECT facts_json FROM legal_agent.legal_agent_run WHERE run_id=%s", (run_id,)).fetchone()
            if existing is None:
                raise KeyError(run_id)
            merged = dict(existing["facts_json"] or {})
            merged.update(facts)
            conn.execute(
                "UPDATE legal_agent.legal_agent_run SET facts_json=%s, updated_at=now() WHERE run_id=%s",
                (Jsonb(merged), run_id),
            )
            for key, value in facts.items():
                conn.execute(
                    """
                    INSERT INTO legal_agent.legal_agent_fact(run_id, fact_key, fact_value, normalized_value, source_type, status)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(run_id, fact_key)
                    DO UPDATE SET fact_value=EXCLUDED.fact_value, normalized_value=EXCLUDED.normalized_value, source_type=EXCLUDED.source_type, updated_at=now()
                    """,
                    (run_id, key, str(value), Jsonb(value), source_type, "confirmed"),
                )
            conn.commit()
            return merged

    def create_uploaded_file(
        self,
        *,
        file_id: str,
        tenant_id: str,
        user_id: str,
        original_filename: str,
        content_type: str | None,
        size_bytes: int,
        sha256_hex: str,
        storage_path: str,
        parse_status: str,
        text_preview: str | None,
        metadata_json: dict[str, Any],
        chunks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                INSERT INTO legal_agent.uploaded_file(
                  file_id, tenant_id, user_id, original_filename, content_type,
                  size_bytes, sha256, storage_path, parse_status, text_preview, metadata_json
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
                """,
                (
                    file_id,
                    tenant_id,
                    user_id,
                    original_filename,
                    content_type,
                    size_bytes,
                    sha256_hex,
                    storage_path,
                    parse_status,
                    text_preview,
                    Jsonb(metadata_json),
                ),
            ).fetchone()
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO legal_agent.uploaded_file_chunk(
                      chunk_id, file_id, chunk_index, page_no, content, citation_anchor, metadata_json
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(chunk_id) DO NOTHING
                    """,
                    (
                        chunk["chunk_id"],
                        file_id,
                        chunk["chunk_index"],
                        chunk.get("page_no"),
                        chunk["content"],
                        chunk["citation_anchor"],
                        Jsonb(chunk.get("metadata") or {}),
                    ),
                )
            conn.commit()
            result = dict(row)
            result["chunk_count"] = len(chunks)
            return result

    def get_uploaded_file(self, file_id: str) -> dict[str, Any] | None:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                SELECT f.*, count(c.chunk_id)::int AS chunk_count
                FROM legal_agent.uploaded_file f
                LEFT JOIN legal_agent.uploaded_file_chunk c ON c.file_id = f.file_id
                WHERE f.file_id=%s
                GROUP BY f.id
                """,
                (file_id,),
            ).fetchone()
            return dict(row) if row else None

    def search_user_material_chunks(self, *, file_ids: list[str], query: str, limit: int = 5) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        keywords = [part for part in query.split() if part]
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                WITH matched AS (
                  SELECT
                    c.chunk_id,
                    c.file_id,
                    f.original_filename,
                    f.storage_path,
                    f.sha256,
                    c.content,
                    c.citation_anchor,
                    c.metadata_json,
                    ts_rank_cd(c.content_tsv, websearch_to_tsquery('simple', %s)) AS text_score,
                    cardinality(
                      ARRAY(
                        SELECT keyword
                        FROM unnest(%s::text[]) AS keyword
                        WHERE c.content ILIKE '%%' || keyword || '%%'
                           OR f.original_filename ILIKE '%%' || keyword || '%%'
                      )
                    ) AS keyword_hits
                  FROM legal_agent.uploaded_file_chunk c
                  JOIN legal_agent.uploaded_file f ON f.file_id = c.file_id
                  WHERE c.file_id = ANY(%s::text[])
                    AND f.parse_status = 'PARSED'
                )
                SELECT
                  chunk_id,
                  file_id,
                  original_filename,
                  storage_path,
                  sha256,
                  content,
                  citation_anchor,
                  metadata_json,
                  (text_score + keyword_hits::numeric) AS score
                FROM matched
                WHERE text_score > 0 OR keyword_hits > 0
                ORDER BY score DESC, chunk_id ASC
                LIMIT %s
                """,
                (query, keywords, file_ids, limit),
            ).fetchall()
            if rows:
                return [dict(row) for row in rows]
            fallback = conn.execute(
                """
                SELECT
                  c.chunk_id,
                  c.file_id,
                  f.original_filename,
                  f.storage_path,
                  f.sha256,
                  c.content,
                  c.citation_anchor,
                  c.metadata_json,
                  0::numeric AS score
                FROM legal_agent.uploaded_file_chunk c
                JOIN legal_agent.uploaded_file f ON f.file_id = c.file_id
                WHERE c.file_id = ANY(%s::text[])
                  AND f.parse_status = 'PARSED'
                ORDER BY c.file_id, c.chunk_index
                LIMIT %s
                """,
                (file_ids, limit),
            ).fetchall()
            return [dict(row) for row in fallback]

    def search_legal_chunks(self, *, query: str, jurisdiction: str, limit: int = 5) -> list[dict[str, Any]]:
        keywords = [part for part in query.split() if part]
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                WITH matched AS (
                  SELECT
                    c.chunk_id,
                    c.doc_id,
                    c.doc_type,
                    c.authority_level,
                    c.title,
                    d.source_url,
                    c.citation_anchor,
                    c.content,
                    c.metadata_json,
                    ts_rank_cd(c.content_tsv, websearch_to_tsquery('simple', %s)) AS text_score,
                    cardinality(
                      ARRAY(
                        SELECT keyword
                        FROM unnest(%s::text[]) AS keyword
                        WHERE c.content ILIKE '%%' || keyword || '%%'
                           OR c.title ILIKE '%%' || keyword || '%%'
                           OR c.citation_anchor ILIKE '%%' || keyword || '%%'
                      )
                    ) AS keyword_hits
                  FROM rag.legal_document_chunk c
                  JOIN rag.legal_source_document d ON d.doc_id = c.doc_id
                  WHERE
                    (c.jurisdiction = %s OR c.jurisdiction = 'CN')
                    AND d.status = 'ACTIVE'
                )
                SELECT
                  chunk_id,
                  doc_id,
                  doc_type,
                  authority_level,
                  title,
                  source_url,
                  citation_anchor,
                  content,
                  metadata_json,
                  (text_score + keyword_hits::numeric) AS score,
                  'full_text' AS retrieval_method
                FROM matched
                WHERE text_score > 0 OR keyword_hits > 0
                ORDER BY
                  CASE authority_level
                    WHEN 'A0' THEN 0
                    WHEN 'A1' THEN 1
                    WHEN 'B0' THEN 2
                    WHEN 'B1' THEN 3
                    ELSE 4
                  END,
                  score DESC,
                  chunk_id ASC
                LIMIT %s
                """,
                (query, keywords, jurisdiction, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def search_case_chunks(self, *, query: str, jurisdiction: str, limit: int = 10) -> list[dict[str, Any]]:
        keywords = [part for part in query.split() if part]
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                WITH matched AS (
                  SELECT
                    c.chunk_id,
                    c.doc_id,
                    c.doc_type,
                    c.authority_level,
                    c.title,
                    d.source_url,
                    d.issuing_authority,
                    c.citation_anchor,
                    c.content,
                    c.metadata_json,
                    ts_rank_cd(c.content_tsv, websearch_to_tsquery('simple', %s)) AS text_score,
                    cardinality(
                      ARRAY(
                        SELECT keyword
                        FROM unnest(%s::text[]) AS keyword
                        WHERE c.content ILIKE '%%' || keyword || '%%'
                           OR c.title ILIKE '%%' || keyword || '%%'
                           OR c.citation_anchor ILIKE '%%' || keyword || '%%'
                           OR c.metadata_json::text ILIKE '%%' || keyword || '%%'
                      )
                    ) AS keyword_hits
                  FROM rag.legal_document_chunk c
                  JOIN rag.legal_source_document d ON d.doc_id = c.doc_id
                  WHERE
                    (c.jurisdiction = %s OR c.jurisdiction = 'CN')
                    AND d.status = 'ACTIVE'
                    AND (
                      c.doc_type IN ('case', 'typical_case', 'guiding_case')
                      OR c.metadata_json ? 'case_id'
                    )
                )
                SELECT
                  chunk_id,
                  doc_id,
                  doc_type,
                  authority_level,
                  title,
                  source_url,
                  issuing_authority,
                  citation_anchor,
                  content,
                  metadata_json,
                  (text_score + keyword_hits::numeric) AS score,
                  'case_full_text' AS retrieval_method
                FROM matched
                WHERE text_score > 0 OR keyword_hits > 0
                ORDER BY
                  CASE authority_level
                    WHEN 'A1' THEN 0
                    WHEN 'B1' THEN 1
                    ELSE 2
                  END,
                  score DESC,
                  chunk_id ASC
                LIMIT %s
                """,
                (query, keywords, jurisdiction, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_retrieval_evidence(self, run_id: str) -> list[dict[str, Any]]:
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                SELECT
                  evidence_id,
                  chunk_id,
                  source_type,
                  authority_level,
                  source_name,
                  source_url,
                  citation_anchor,
                  quote,
                  supported_claim,
                  score,
                  retrieval_method,
                  metadata_json,
                  created_at
                FROM legal_agent.retrieval_evidence
                WHERE run_id=%s
                ORDER BY evidence_id ASC
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def replace_retrieval_evidence(self, run_id: str, evidence_pack: list[dict[str, Any]]) -> None:
        with connect(self.settings) as conn:
            conn.execute("DELETE FROM legal_agent.retrieval_evidence WHERE run_id=%s", (run_id,))
            for evidence in evidence_pack:
                conn.execute(
                    """
                    INSERT INTO legal_agent.retrieval_evidence(
                      run_id, evidence_id, chunk_id, source_type, authority_level, source_name,
                      source_url, citation_anchor, quote, supported_claim, score, retrieval_method,
                      metadata_json
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        run_id,
                        evidence["evidence_id"],
                        evidence.get("chunk_id"),
                        evidence.get("source_type", "legal_document"),
                        evidence["authority_level"],
                        evidence["source_name"],
                        evidence.get("source_url"),
                        evidence.get("citation_anchor"),
                        evidence["quote"],
                        evidence.get("supported_claim"),
                        evidence.get("score"),
                        evidence.get("retrieval_method"),
                        Jsonb(evidence.get("metadata") or {}),
                    ),
                )
            conn.commit()

    def upsert_approval_request(
        self,
        *,
        approval_id: str,
        run_id: str,
        agentledger_run_id: str,
        agentledger_approval_id: str,
        approval_key: str,
        status: str,
        risk_level: str,
        reason: str,
        request_json: dict[str, Any],
        review_result_json: dict[str, Any],
        document_json: dict[str, Any],
        requested_by: str,
    ) -> dict[str, Any]:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                INSERT INTO legal_agent.approval_request(
                  approval_id, run_id, agentledger_run_id, agentledger_approval_id, approval_key,
                  status, risk_level, reason, request_json, review_result_json,
                  document_json, requested_by
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(approval_key) DO UPDATE SET
                  status=EXCLUDED.status,
                  risk_level=EXCLUDED.risk_level,
                  reason=EXCLUDED.reason,
                  request_json=EXCLUDED.request_json,
                  review_result_json=EXCLUDED.review_result_json,
                  document_json=EXCLUDED.document_json,
                  requested_by=EXCLUDED.requested_by,
                  updated_at=now()
                RETURNING *
                """,
                (
                    approval_id,
                    run_id,
                    agentledger_run_id,
                    agentledger_approval_id,
                    approval_key,
                    status,
                    risk_level,
                    reason,
                    Jsonb(request_json),
                    Jsonb(review_result_json),
                    Jsonb(document_json),
                    requested_by,
                ),
            ).fetchone()
            conn.commit()
            return dict(row)

    def list_approval_requests(self, run_id: str) -> list[dict[str, Any]]:
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                SELECT approval_id, agentledger_approval_id, approval_key, status, risk_level,
                       reason, request_json, review_result_json, document_json,
                       requested_by, decided_by, decision_reason, created_at, updated_at, decided_at
                FROM legal_agent.approval_request
                WHERE run_id=%s
                ORDER BY created_at DESC
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_approval_request(self, run_id: str, approval_id: str) -> dict[str, Any] | None:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM legal_agent.approval_request
                WHERE run_id=%s AND approval_id=%s
                """,
                (run_id, approval_id),
            ).fetchone()
            return dict(row) if row else None

    def decide_approval_request(self, run_id: str, approval_id: str, *, approved: bool, approver: str, reason: str) -> dict[str, Any]:
        status = "APPROVED" if approved else "DENIED"
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                UPDATE legal_agent.approval_request
                   SET status=%s,
                       decided_by=%s,
                       decision_reason=%s,
                       decided_at=now(),
                       updated_at=now()
                 WHERE run_id=%s AND approval_id=%s
                RETURNING *
                """,
                (status, approver, reason, run_id, approval_id),
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            conn.commit()
            return dict(row)

    def expire_approval_request(self, run_id: str, approval_id: str, *, reason: str = "approval_timeout") -> dict[str, Any]:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                UPDATE legal_agent.approval_request
                   SET status='EXPIRED',
                       decided_by='system-timeout',
                       decision_reason=%s,
                       decided_at=now(),
                       updated_at=now()
                 WHERE run_id=%s
                   AND approval_id=%s
                   AND status='PENDING'
                RETURNING *
                """,
                (reason, run_id, approval_id),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    """
                    SELECT *
                    FROM legal_agent.approval_request
                    WHERE run_id=%s AND approval_id=%s
                    """,
                    (run_id, approval_id),
                ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            conn.commit()
            return dict(row)

    def upsert_generated_document(
        self,
        *,
        run_id: str,
        document_id: str,
        document_type: str,
        jurisdiction: str,
        title: str,
        status: str,
        document_json: dict[str, Any],
        markdown: str,
        markdown_path: str,
        docx_path: str,
        facts_json: dict[str, Any],
        claims_json: list[str],
        legal_basis_json: list[str],
        evidence_list_json: list[str],
        amount_calculation_json: dict[str, Any],
        risk_notice_json: list[str],
        review_result_json: dict[str, Any],
        agentledger_artifact_id: str | None,
        agentledger_blob_ref: str | None,
    ) -> dict[str, Any]:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                INSERT INTO legal_agent.generated_legal_document(
                  run_id, document_id, document_type, jurisdiction, title, status, document_json,
                  markdown, markdown_path, docx_path, facts_json, claims_json, legal_basis_json,
                  evidence_list_json, amount_calculation_json, risk_notice_json,
                  review_result_json, agentledger_artifact_id, agentledger_blob_ref
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(document_id) DO UPDATE SET
                  status=EXCLUDED.status,
                  document_json=EXCLUDED.document_json,
                  markdown=EXCLUDED.markdown,
                  markdown_path=EXCLUDED.markdown_path,
                  docx_path=EXCLUDED.docx_path,
                  facts_json=EXCLUDED.facts_json,
                  claims_json=EXCLUDED.claims_json,
                  legal_basis_json=EXCLUDED.legal_basis_json,
                  evidence_list_json=EXCLUDED.evidence_list_json,
                  amount_calculation_json=EXCLUDED.amount_calculation_json,
                  risk_notice_json=EXCLUDED.risk_notice_json,
                  review_result_json=EXCLUDED.review_result_json,
                  agentledger_artifact_id=EXCLUDED.agentledger_artifact_id,
                  agentledger_blob_ref=EXCLUDED.agentledger_blob_ref,
                  updated_at=now()
                RETURNING *
                """,
                (
                    run_id,
                    document_id,
                    document_type,
                    jurisdiction,
                    title,
                    status,
                    Jsonb(document_json),
                    markdown,
                    markdown_path,
                    docx_path,
                    Jsonb(facts_json),
                    Jsonb(claims_json),
                    Jsonb(legal_basis_json),
                    Jsonb(evidence_list_json),
                    Jsonb(amount_calculation_json),
                    Jsonb(risk_notice_json),
                    Jsonb(review_result_json),
                    agentledger_artifact_id,
                    agentledger_blob_ref,
                ),
            ).fetchone()
            conn.commit()
            return dict(row)

    def list_generated_documents(self, run_id: str) -> list[dict[str, Any]]:
        with connect(self.settings) as conn:
            rows = conn.execute(
                """
                SELECT document_id, document_type, jurisdiction, title, status,
                       markdown_path, docx_path, agentledger_artifact_id, agentledger_blob_ref,
                       created_at, updated_at
                FROM legal_agent.generated_legal_document
                WHERE run_id=%s
                ORDER BY created_at DESC
                """,
                (run_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_generated_document(self, run_id: str, document_id: str) -> dict[str, Any] | None:
        with connect(self.settings) as conn:
            row = conn.execute(
                """
                SELECT *
                FROM legal_agent.generated_legal_document
                WHERE run_id=%s AND document_id=%s
                """,
                (run_id, document_id),
            ).fetchone()
            return dict(row) if row else None


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _json_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
