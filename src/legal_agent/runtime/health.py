from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import uuid4

from legal_agent.core.config import Settings
from legal_agent.db.connection import connect
from legal_agent.runtime.tracing import probe_langfuse
from legal_agent.workflows.client import run_health_check_workflow, temporal_client
from legal_agent.workflows.search_attributes import ensure_temporal_search_attributes


def _check(name: str, status: str, *, required: bool = True, details: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "required": required,
        "details": details or {},
    }
    if error:
        payload["error"] = error
    return payload


def _error(name: str, exc: BaseException, *, required: bool = True, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return _check(name, "failed", required=required, details=details, error=f"{type(exc).__name__}: {exc}")


def _regclass_exists(conn: Any, qualified_name: str) -> bool:
    row = conn.execute("SELECT to_regclass(%s) IS NOT NULL AS exists", (qualified_name,)).fetchone()
    return bool(row and row["exists"])


def _writable_dir_smoke(path: Path) -> dict[str, Any]:
    path.mkdir(parents=True, exist_ok=True)
    marker = path / f".health-{uuid4().hex}.tmp"
    marker.write_text("ok", encoding="utf-8")
    content = marker.read_text(encoding="utf-8")
    marker.unlink()
    return {"path": str(path), "writable": content == "ok"}


def _check_database(settings: Settings) -> dict[str, Any]:
    try:
        with connect(settings) as conn:
            row = conn.execute(
                """
                SELECT current_database() AS database_name,
                       current_schema() AS schema_name,
                       version() AS version
                """
            ).fetchone()
            required_tables = [
                "legal_agent.legal_agent_run",
                "legal_agent.api_idempotency_key",
                "rag.legal_source_document",
                "rag.legal_document_chunk",
            ]
            missing_tables = [name for name in required_tables if not _regclass_exists(conn, name)]
        details = {
            "database": row["database_name"] if row else None,
            "schema": row["schema_name"] if row else None,
            "required_tables": required_tables,
            "missing_tables": missing_tables,
        }
        return _check("database", "ok" if not missing_tables else "failed", details=details)
    except Exception as exc:
        return _error("database", exc)


async def _check_temporal_server(settings: Settings) -> dict[str, Any]:
    try:
        await asyncio.wait_for(temporal_client(settings), timeout=5)
        return _check(
            "temporal_server",
            "ok",
            details={
                "address": settings.temporal_address,
                "namespace": settings.temporal_namespace,
            },
        )
    except Exception as exc:
        return _error(
            "temporal_server",
            exc,
            details={
                "address": settings.temporal_address,
                "namespace": settings.temporal_namespace,
            },
        )


async def _check_temporal_worker(settings: Settings, *, name: str = "temporal_worker", task_queue: str | None = None, worker: str = "legal-agent-worker") -> dict[str, Any]:
    queue = task_queue or settings.temporal_task_queue
    try:
        result = await asyncio.wait_for(run_health_check_workflow(settings, task_queue=queue, worker=worker), timeout=12)
        return _check(
            name,
            "ok" if result.get("status") == "ok" else "failed",
            details={
                "task_queue": queue,
                "result": result,
            },
        )
    except Exception as exc:
        return _error(name, exc, details={"task_queue": queue})


async def _check_temporal_search_attributes(settings: Settings) -> dict[str, Any]:
    if not settings.temporal_search_attributes_enabled:
        return _check(
            "temporal_search_attributes",
            "skipped",
            required=False,
            details={"enabled": False},
        )
    try:
        client = await asyncio.wait_for(temporal_client(settings), timeout=5)
        status = await asyncio.wait_for(ensure_temporal_search_attributes(settings, client), timeout=5)
        return _check(
            "temporal_search_attributes",
            "ok" if status.get("registered") else "failed",
            details=status,
            error=None if status.get("registered") else "missing required Temporal search attributes",
        )
    except Exception as exc:
        return _error(
            "temporal_search_attributes",
            exc,
            details={
                "enabled": settings.temporal_search_attributes_enabled,
                "namespace": settings.temporal_namespace,
            },
        )


def _check_rag(settings: Settings) -> dict[str, Any]:
    try:
        with connect(settings) as conn:
            required_tables = [
                "rag.rag_ingest_run",
                "rag.legal_source_document",
                "rag.legal_document_chunk",
            ]
            missing_tables = [name for name in required_tables if not _regclass_exists(conn, name)]
            if missing_tables:
                return _check("rag", "failed", details={"missing_tables": missing_tables})
            corpus = conn.execute(
                """
                SELECT
                  (SELECT count(*)::int FROM rag.legal_source_document WHERE status='ACTIVE') AS active_documents,
                  (SELECT count(*)::int FROM rag.legal_document_chunk) AS chunks,
                  (SELECT count(*)::int FROM rag.legal_document_chunk WHERE embedding IS NOT NULL) AS embedded_chunks
                """
            ).fetchone()
            latest_ingest = conn.execute(
                """
                SELECT ingest_id, domain, status, stats_json, finished_at
                FROM rag.rag_ingest_run
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            snapshot_row = conn.execute(
                """
                SELECT snapshot_path
                FROM rag.legal_source_document
                WHERE status='ACTIVE' AND snapshot_path IS NOT NULL
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()
            keyword_index_ready = _regclass_exists(conn, "rag.idx_legal_document_chunk_tsv")
            vector_index_ready = _regclass_exists(conn, "rag.idx_legal_document_chunk_embedding")

        snapshot_path = str(snapshot_row["snapshot_path"]) if snapshot_row and snapshot_row["snapshot_path"] else ""
        snapshot_file = Path(snapshot_path) if snapshot_path else None
        snapshot_readable = bool(snapshot_file and snapshot_file.is_file())
        details = {
            "manifest": str(settings.rag_source_manifest),
            "seed_file": str(settings.rag_seed_file),
            "active_documents": int(corpus["active_documents"] if corpus else 0),
            "chunks": int(corpus["chunks"] if corpus else 0),
            "embedded_chunks": int(corpus["embedded_chunks"] if corpus else 0),
            "keyword_index_ready": keyword_index_ready,
            "vector_index_ready": vector_index_ready,
            "latest_ingest": dict(latest_ingest) if latest_ingest else None,
            "snapshot_path": snapshot_path,
            "snapshot_readable": snapshot_readable,
        }
        healthy = (
            details["active_documents"] > 0
            and details["chunks"] > 0
            and details["embedded_chunks"] > 0
            and keyword_index_ready
            and vector_index_ready
            and snapshot_readable
            and bool(latest_ingest and latest_ingest["status"] == "SUCCEEDED")
        )
        return _check("rag", "ok" if healthy else "failed", details=details)
    except Exception as exc:
        return _error("rag", exc)


def _check_agentledger(settings: Settings) -> dict[str, Any]:
    schema_name = settings.agentledger_postgres_schema
    table_names = ["runs", "steps", "events", "tool_ledger", "artifacts", "approval_requests", "cost_records"]
    try:
        import agentledger  # noqa: F401

        with connect(settings) as conn:
            missing_tables = [name for name in table_names if not _regclass_exists(conn, f"{schema_name}.{name}")]
            columns = conn.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema=%s AND table_name='runs'
                """,
                (schema_name,),
            ).fetchall()
        run_columns = {str(row["column_name"]) for row in columns}
        missing_run_columns = sorted({"run_id", "state_json", "state_version"} - run_columns)
        blob_smoke = _writable_dir_smoke(settings.agentledger_blob_dir)
        details = {
            "schema": schema_name,
            "tables": table_names,
            "missing_tables": missing_tables,
            "missing_run_columns": missing_run_columns,
            "blob_store": blob_smoke,
        }
        healthy = not missing_tables and not missing_run_columns and blob_smoke["writable"]
        return _check("agentledger", "ok" if healthy else "failed", details=details)
    except Exception as exc:
        return _error("agentledger", exc, details={"schema": schema_name})


def _check_shared_volume(settings: Settings) -> dict[str, Any]:
    try:
        data_smoke = _writable_dir_smoke(settings.data_dir)
        generated_smoke = _writable_dir_smoke(settings.data_dir / "generated-documents")
        upload_smoke = _writable_dir_smoke(settings.data_dir / "uploaded-files")
        details = {
            "data_dir": data_smoke,
            "generated_documents_dir": generated_smoke,
            "uploaded_files_dir": upload_smoke,
        }
        healthy = data_smoke["writable"] and generated_smoke["writable"] and upload_smoke["writable"]
        return _check("shared_volume", "ok" if healthy else "failed", details=details)
    except Exception as exc:
        return _error("shared_volume", exc, details={"data_dir": str(settings.data_dir)})


def _check_llm(settings: Settings) -> dict[str, Any]:
    details = {
        "enabled": settings.llm_enabled,
        "provider": settings.llm_provider,
        "base_url_configured": bool(settings.llm_base_url),
        "api_key_configured": bool(settings.llm_api_key),
        "model": settings.llm_model or None,
        "timeout_seconds": settings.llm_timeout_seconds,
        "temperature": settings.llm_temperature,
        "runtime_probe": "disabled",
    }
    if not settings.llm_enabled:
        return _check("llm", "skipped", required=False, details=details)
    missing = [
        name
        for name, value in {
            "LEGAL_AGENT_LLM_BASE_URL": settings.llm_base_url,
            "LEGAL_AGENT_LLM_API_KEY": settings.llm_api_key,
            "LEGAL_AGENT_LLM_MODEL": settings.llm_model,
        }.items()
        if not value
    ]
    if settings.llm_provider != "openai_compatible":
        return _check("llm", "failed", details=details, error=f"unsupported provider: {settings.llm_provider}")
    if missing:
        return _check("llm", "failed", details=details, error=f"missing config: {', '.join(missing)}")
    return _check("llm", "ok", details=details)


def _check_langfuse(settings: Settings) -> dict[str, Any]:
    details = {
        "enabled": settings.langfuse_enabled,
        "host": settings.langfuse_host,
        "project": settings.langfuse_project,
        "public_key_configured": bool(settings.langfuse_public_key),
        "secret_key_configured": bool(settings.langfuse_secret_key),
        "runtime_probe": "disabled",
    }
    if not settings.langfuse_enabled:
        return _check("langfuse", "skipped", required=False, details=details)
    missing = [
        name
        for name, value in {
            "LANGFUSE_PUBLIC_KEY": settings.langfuse_public_key,
            "LANGFUSE_SECRET_KEY": settings.langfuse_secret_key,
        }.items()
        if not value
    ]
    if missing:
        return _check("langfuse", "failed", details=details, error=f"missing config: {', '.join(missing)}")
    try:
        probe = probe_langfuse(settings)
    except Exception as exc:
        return _error("langfuse", exc, details=details)
    details["probe"] = probe
    if not probe.get("auth_ok"):
        return _check("langfuse", "failed", details=details, error=str(probe.get("error") or "auth_check failed"))
    return _check("langfuse", "ok", details=details)


def _overall_status(checks: list[dict[str, Any]]) -> str:
    required_checks = [check for check in checks if check.get("required", True)]
    if any(check["status"] == "failed" for check in required_checks):
        return "failed"
    if any(check["status"] == "degraded" for check in required_checks):
        return "degraded"
    return "ok"


async def detailed_health(settings: Settings) -> dict[str, Any]:
    checks = [
        _check_database(settings),
        await _check_temporal_server(settings),
        await _check_temporal_worker(settings),
        await _check_temporal_search_attributes(settings),
        await _check_temporal_worker(
            settings,
            name="rag_worker",
            task_queue=settings.temporal_rag_task_queue,
            worker="rag-worker",
        ),
        await _check_temporal_worker(
            settings,
            name="embedding_worker",
            task_queue=settings.temporal_embedding_task_queue,
            worker="embedding-worker",
        ),
        _check_rag(settings),
        _check_agentledger(settings),
        _check_shared_volume(settings),
        _check_llm(settings),
        _check_langfuse(settings),
    ]
    return {
        "status": _overall_status(checks),
        "env": settings.env,
        "checks": checks,
        "summary": {
            "ok": sum(1 for check in checks if check["status"] == "ok"),
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "skipped": sum(1 for check in checks if check["status"] == "skipped"),
        },
    }
