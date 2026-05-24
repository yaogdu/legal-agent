from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from legal_agent.api.idempotency import begin_idempotent_request, complete_idempotent_request
from legal_agent.core.config import Settings, load_settings
from legal_agent.core.enums import NodeName, NodeStatus, RunStatus
from legal_agent.core.facts import infer_facts_from_input, merge_inferred_claims, missing_fact_fields, question_groups_for_missing_fields, questions_for_missing_fields
from legal_agent.core.ids import new_id
from legal_agent.core.models import (
    AddFactsRequest,
    AddFactsResponse,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    CancelRunRequest,
    CancelRunResponse,
    CaseSearchRequest,
    CreateRunRequest,
    CreateRunResponse,
    RunStatusResponse,
    UploadFileResponse,
)
from legal_agent.db.migrate import migrate
from legal_agent.db.repository import RunRepository
from legal_agent.files.parser import parse_and_store_upload
from legal_agent.llm.client import extract_labor_claims_result
from legal_agent.runtime.agentledger import decide_agentledger_approval
from legal_agent.runtime.agentledger import create_agentledger_run
from legal_agent.runtime.agentledger import patch_agentledger_state
from legal_agent.runtime.health import detailed_health
from legal_agent.workflows.client import cancel_legal_workflow, signal_approval, signal_facts, start_legal_workflow, workflow_id_for_run


TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED.value,
    RunStatus.CANCELLED.value,
    RunStatus.APPROVAL_REJECTED.value,
    RunStatus.FAILED.value,
    RunStatus.EXPIRED.value,
}


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="Legal Agent", version="0.1.0")
    static_dir = Path(__file__).resolve().parents[1] / "web"
    if static_dir.exists():
        app.mount("/app", StaticFiles(directory=static_dir, html=True), name="legal-agent-web")

    @app.get("/")
    async def index() -> RedirectResponse:
        return RedirectResponse(url="/app/")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "env": settings.env}

    @app.get("/healthz/details")
    async def healthz_details() -> dict[str, object]:
        return await detailed_health(settings)

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        metrics_data = RunRepository(settings).collect_metrics()
        return PlainTextResponse(
            _prometheus_metrics(settings, metrics_data),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post("/admin/migrate")
    async def admin_migrate() -> dict[str, str]:
        migrate(settings)
        return {"status": "ok"}

    @app.get("/api/v1/legal-agent/runs")
    async def list_runs(limit: int = 20) -> dict[str, object]:
        bounded_limit = max(1, min(limit, 100))
        return {
            "request_id": new_id("req"),
            "runs": RunRepository(settings).list_runs(limit=bounded_limit),
        }

    @app.post("/api/v1/legal-agent/files", response_model=UploadFileResponse)
    async def upload_file(file: UploadFile = File(...), idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> UploadFileResponse:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty file")
        repo = RunRepository(settings)
        guard = begin_idempotent_request(
            repo,
            scope="POST /api/v1/legal-agent/files",
            raw_key=idempotency_key,
            body={
                "filename": file.filename or "upload.txt",
                "content_type": file.content_type,
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            },
        )
        if guard.replay_response is not None:
            return guard.replay_response
        parsed = parse_and_store_upload(settings, filename=file.filename or "upload.txt", content_type=file.content_type, data=data)
        row = repo.create_uploaded_file(
            file_id=parsed.file_id,
            tenant_id="default",
            user_id="demo-user",
            original_filename=parsed.original_filename,
            content_type=parsed.content_type,
            size_bytes=parsed.size_bytes,
            sha256_hex=parsed.sha256_hex,
            storage_path=parsed.storage_path,
            parse_status=parsed.parse_status,
            text_preview=parsed.text_preview,
            metadata_json=parsed.metadata,
            chunks=parsed.chunks,
        )
        response = UploadFileResponse(
            request_id=new_id("req"),
            file_id=parsed.file_id,
            original_filename=parsed.original_filename,
            content_type=parsed.content_type,
            size_bytes=parsed.size_bytes,
            sha256=parsed.sha256_hex,
            parse_status=parsed.parse_status,
            chunk_count=int(row.get("chunk_count") or 0),
            text_preview=parsed.text_preview,
        ).model_dump(mode="json")
        return complete_idempotent_request(repo, guard, response)

    @app.get("/api/v1/legal-agent/files/{file_id}")
    async def get_file(file_id: str) -> dict[str, object]:
        row = RunRepository(settings).get_uploaded_file(file_id)
        if row is None:
            raise HTTPException(status_code=404, detail="file not found")
        return {"request_id": new_id("req"), "file": row}

    @app.post("/api/v1/legal-agent/cases/search")
    async def search_cases(req: CaseSearchRequest) -> dict[str, object]:
        query = req.query or " ".join([req.domain, req.region, *req.claims])
        rows = RunRepository(settings).search_case_chunks(query=query, jurisdiction=req.region, limit=req.top_k)
        cases = []
        for row in rows:
            metadata = dict(row.get("metadata_json") or {})
            cases.append(
                {
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
            )
        return {
            "request_id": new_id("req"),
            "domain": req.domain,
            "query": query,
            "cases": cases,
        }

    @app.post("/api/v1/legal-agent/runs", response_model=CreateRunResponse)
    async def create_run(req: CreateRunRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> CreateRunResponse:
        repo = RunRepository(settings)
        guard = begin_idempotent_request(
            repo,
            scope="POST /api/v1/legal-agent/runs",
            raw_key=idempotency_key,
            body=req.model_dump(mode="json"),
        )
        if guard.replay_response is not None:
            return guard.replay_response
        request_id = new_id("req")
        run_id = new_id("run")
        workflow_id = workflow_id_for_run(run_id)
        tenant_id = "default"
        user_id = "demo-user"
        risk_level = "L2"
        preflight_facts = infer_facts_from_input(req.input.text, req.input.file_ids)
        claim_extraction = await extract_labor_claims_result(
            settings,
            user_input=req.input.text,
            metadata={"run_id": run_id, "temporal_workflow_id": workflow_id, "node": "PREFLIGHT"},
        )
        claim_patch = merge_inferred_claims(preflight_facts, claim_extraction.claims)
        if claim_patch:
            preflight_facts = {**preflight_facts, **claim_patch}
        preflight_missing = missing_fact_fields(preflight_facts, req.task_type)
        initial_state = {
            "legal_agent_run_id": run_id,
            "request_id": request_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "task_type": req.task_type,
            "legal_domain": req.legal_domain,
            "jurisdiction": req.jurisdiction,
            "risk_level": risk_level,
            "input": req.model_dump(mode="json"),
            "initial_facts": preflight_facts,
            "initial_claim_extraction": claim_extraction.to_audit_payload(),
            "missing_fields": preflight_missing,
        }
        agentledger_run_id = create_agentledger_run(settings, initial_state)
        repo.create_run(
            run_id=run_id,
            agentledger_run_id=agentledger_run_id,
            request_id=request_id,
            temporal_workflow_id=workflow_id,
            tenant_id=tenant_id,
            user_id=user_id,
            task_type=req.task_type,
            legal_domain=req.legal_domain,
            jurisdiction=req.jurisdiction,
            risk_level=risk_level,
            run_status=RunStatus.CREATED,
            current_node=NodeName.CLASSIFY,
            current_node_status=NodeStatus.PENDING,
            input_json=req.model_dump(mode="json"),
            missing_fields=preflight_missing,
        )
        if preflight_facts:
            repo.merge_facts(run_id, preflight_facts, source_type="system_inferred")
        payload = {
            "run_id": run_id,
            "agentledger_run_id": agentledger_run_id,
            "request_id": request_id,
            "temporal_workflow_id": workflow_id,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "agent_task_queue": settings.temporal_task_queue,
            "rag_task_queue": settings.temporal_rag_task_queue,
            "embedding_task_queue": settings.temporal_embedding_task_queue,
            "user_input_timeout_seconds": req.user_input_timeout_seconds or settings.user_input_timeout_seconds,
            "approval_timeout_seconds": req.output_options.approval_timeout_seconds or settings.approval_timeout_seconds,
            "task_type": req.task_type,
            "legal_domain": req.legal_domain,
            "jurisdiction": req.jurisdiction,
            "risk_level": risk_level,
            "run_status": RunStatus.CREATED.value,
        }
        if settings.temporal_start_workflows:
            await start_legal_workflow(settings, payload)
        response = CreateRunResponse(
            request_id=request_id,
            run_id=run_id,
            agentledger_run_id=agentledger_run_id,
            run_status=RunStatus.CREATED,
            current_node=NodeName.CLASSIFY,
            missing_fields=preflight_missing,
            questions=questions_for_missing_fields(preflight_missing),
            question_groups=question_groups_for_missing_fields(preflight_missing),
        ).model_dump(mode="json")
        return complete_idempotent_request(repo, guard, response)

    @app.get("/api/v1/legal-agent/runs/{run_id}", response_model=RunStatusResponse)
    async def get_run(run_id: str) -> RunStatusResponse:
        row = RunRepository(settings).get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        missing = row.get("missing_fields_json") or []
        return RunStatusResponse(
            request_id=new_id("req"),
            run_id=run_id,
            run_status=RunStatus(row["run_status"]),
            current_node=NodeName(row["current_node"]) if row.get("current_node") else None,
            current_node_status=NodeStatus(row["current_node_status"]) if row.get("current_node_status") else None,
            progress=_progress(row.get("current_node")),
            missing_fields=missing,
            questions=questions_for_missing_fields(missing),
            question_groups=question_groups_for_missing_fields(missing),
            requires_user_input=row["run_status"] == RunStatus.WAITING_USER_INPUT.value,
            requires_approval=row["run_status"] == RunStatus.WAITING_APPROVAL.value,
            last_error=row.get("last_error"),
            updated_at=row.get("updated_at") if isinstance(row.get("updated_at"), datetime) else None,
        )

    @app.post("/api/v1/legal-agent/runs/{run_id}/facts", response_model=AddFactsResponse)
    async def add_facts(run_id: str, req: AddFactsRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> AddFactsResponse:
        repo = RunRepository(settings)
        row = repo.get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        guard = begin_idempotent_request(
            repo,
            scope="POST /api/v1/legal-agent/runs/{run_id}/facts",
            raw_key=idempotency_key,
            body={"run_id": run_id, **req.model_dump(mode="json")},
        )
        if guard.replay_response is not None:
            return guard.replay_response
        if row["run_status"] in TERMINAL_RUN_STATUSES:
            raise HTTPException(status_code=409, detail=f"run is terminal: {row['run_status']}")
        repo.merge_facts(run_id, req.facts)
        repo.update_status(run_id, run_status=RunStatus.RUNNING, current_node=NodeName.FACT_CHECK, current_node_status=NodeStatus.RUNNING)
        if settings.temporal_start_workflows:
            await signal_facts(settings, run_id, req.facts)
        response = AddFactsResponse(
            request_id=new_id("req"),
            run_id=run_id,
            run_status=RunStatus.RUNNING,
            current_node=NodeName.FACT_CHECK,
            next_actions=[],
        ).model_dump(mode="json")
        return complete_idempotent_request(repo, guard, response)

    @app.get("/api/v1/legal-agent/runs/{run_id}/approvals")
    async def list_approvals(run_id: str) -> dict[str, object]:
        repo = RunRepository(settings)
        if repo.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "request_id": new_id("req"),
            "run_id": run_id,
            "approvals": repo.list_approval_requests(run_id),
        }

    @app.post("/api/v1/legal-agent/runs/{run_id}/approvals/{approval_id}", response_model=ApprovalDecisionResponse)
    async def decide_approval(
        run_id: str,
        approval_id: str,
        req: ApprovalDecisionRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> ApprovalDecisionResponse:
        repo = RunRepository(settings)
        approval = repo.get_approval_request(run_id, approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="approval not found")
        guard = begin_idempotent_request(
            repo,
            scope="POST /api/v1/legal-agent/runs/{run_id}/approvals/{approval_id}",
            raw_key=idempotency_key,
            body={"run_id": run_id, "approval_id": approval_id, **req.model_dump(mode="json")},
        )
        if guard.replay_response is not None:
            return guard.replay_response
        if approval.get("status") != "PENDING":
            raise HTTPException(status_code=409, detail=f"approval is not pending: {approval.get('status')}")
        decision = {
            "approval_id": approval_id,
            "approved": req.approved,
            "approver": req.approver,
            "reason": req.reason,
        }
        if settings.temporal_start_workflows:
            await signal_approval(settings, run_id, decision)
            run_status = RunStatus.RUNNING if req.approved else RunStatus.APPROVAL_REJECTED
            current_node = NodeName.APPROVAL
            approval_status = "APPROVED" if req.approved else "DENIED"
        else:
            decide_agentledger_approval(
                settings,
                agentledger_approval_id=str(approval["agentledger_approval_id"]),
                approved=req.approved,
                approver=req.approver,
                reason=req.reason,
            )
            repo.decide_approval_request(run_id, approval_id, approved=req.approved, approver=req.approver, reason=req.reason)
            run_status = RunStatus.RUNNING if req.approved else RunStatus.APPROVAL_REJECTED
            current_node = NodeName.OUTPUT if req.approved else NodeName.APPROVAL
            approval_status = "APPROVED" if req.approved else "DENIED"
        response = ApprovalDecisionResponse(
            request_id=new_id("req"),
            run_id=run_id,
            approval_id=approval_id,
            run_status=run_status,
            current_node=current_node,
            approval_status=approval_status,
        ).model_dump(mode="json")
        return complete_idempotent_request(repo, guard, response)

    @app.post("/api/v1/legal-agent/runs/{run_id}/cancel", response_model=CancelRunResponse)
    async def cancel_run(
        run_id: str,
        req: CancelRunRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> CancelRunResponse:
        repo = RunRepository(settings)
        row = repo.get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        guard = begin_idempotent_request(
            repo,
            scope="POST /api/v1/legal-agent/runs/{run_id}/cancel",
            raw_key=idempotency_key,
            body={"run_id": run_id, **req.model_dump(mode="json")},
        )
        if guard.replay_response is not None:
            return guard.replay_response

        current_status = str(row["run_status"])
        reason = req.reason.strip() or "user_cancelled"
        cancellation_status = "already_terminal"
        if current_status not in TERMINAL_RUN_STATUSES:
            if settings.temporal_start_workflows:
                await cancel_legal_workflow(settings, run_id)
            cancellation = {
                "status": "CANCELLED",
                "reason": reason,
                "requested_by": req.requested_by,
                "previous_status": current_status,
                "previous_node": row.get("current_node"),
            }
            repo.update_status(
                run_id,
                run_status=RunStatus.CANCELLED,
                result_summary={"cancellation": cancellation},
                last_error=reason,
            )
            patch_agentledger_state(
                settings,
                str(row["agentledger_run_id"]),
                {"cancellation": cancellation},
                "cancel run",
            )
            row = repo.get_run(run_id) or row
            cancellation_status = "cancelled"

        response = CancelRunResponse(
            request_id=new_id("req"),
            run_id=run_id,
            run_status=RunStatus(row["run_status"]),
            current_node=NodeName(row["current_node"]) if row.get("current_node") else None,
            cancellation_status=cancellation_status,
            reason=reason,
        ).model_dump(mode="json")
        return complete_idempotent_request(repo, guard, response)

    @app.get("/api/v1/legal-agent/runs/{run_id}/audit")
    async def get_audit(run_id: str) -> dict[str, object]:
        audit = RunRepository(settings).get_run_audit(run_id)
        if audit is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "request_id": new_id("req"),
            **audit,
        }

    @app.get("/api/v1/legal-agent/runs/{run_id}/replay")
    async def get_replay(run_id: str) -> dict[str, object]:
        audit = RunRepository(settings).get_run_audit(run_id)
        if audit is None:
            raise HTTPException(status_code=404, detail="run not found")
        agentledger_run = audit.get("agentledger_run") or {}
        business_run = audit.get("business_run") or {}
        return {
            "request_id": new_id("req"),
            "run_id": audit["run_id"],
            "agentledger_run_id": audit["agentledger_run_id"],
            "business_status": business_run.get("run_status") if isinstance(business_run, dict) else None,
            "agentledger_status": agentledger_run.get("status") if isinstance(agentledger_run, dict) else None,
            "state_version": agentledger_run.get("state_version") if isinstance(agentledger_run, dict) else None,
            "summary": audit.get("summary") or {},
            "timeline": _replay_timeline(audit),
            "tool_calls": _tool_replay(audit),
            "approvals": _approval_replay(audit),
            "artifacts": _artifact_replay(audit),
            "final_state": agentledger_run.get("state_json") if isinstance(agentledger_run, dict) else {},
        }

    @app.get("/api/v1/legal-agent/runs/{run_id}/result")
    async def get_result(run_id: str) -> dict[str, object]:
        row = RunRepository(settings).get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "request_id": new_id("req"),
            "run_id": run_id,
            "run_status": row["run_status"],
            "result": row.get("result_summary_json") or {},
        }

    @app.get("/api/v1/legal-agent/runs/{run_id}/evidence")
    async def get_evidence(run_id: str) -> dict[str, object]:
        repo = RunRepository(settings)
        if repo.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "request_id": new_id("req"),
            "run_id": run_id,
            "evidence_pack": repo.list_retrieval_evidence(run_id),
        }

    @app.get("/api/v1/legal-agent/runs/{run_id}/draft")
    async def get_draft(run_id: str) -> dict[str, object]:
        draft = RunRepository(settings).get_run_draft(run_id)
        if draft is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "request_id": new_id("req"),
            **draft,
        }

    @app.get("/api/v1/legal-agent/runs/{run_id}/documents")
    async def list_documents(run_id: str) -> dict[str, object]:
        repo = RunRepository(settings)
        if repo.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "request_id": new_id("req"),
            "run_id": run_id,
            "documents": repo.list_generated_documents(run_id),
        }

    @app.get("/api/v1/legal-agent/runs/{run_id}/documents/{document_id}")
    async def get_document(run_id: str, document_id: str) -> dict[str, object]:
        row = RunRepository(settings).get_generated_document(run_id, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        return {
            "request_id": new_id("req"),
            "run_id": run_id,
            "document_id": document_id,
            "document": row.get("document_json") or {},
            "markdown": row.get("markdown"),
            "markdown_path": row.get("markdown_path"),
            "docx_path": row.get("docx_path"),
            "agentledger_artifact_id": row.get("agentledger_artifact_id"),
            "agentledger_blob_ref": row.get("agentledger_blob_ref"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }

    @app.get("/api/v1/legal-agent/runs/{run_id}/documents/{document_id}/markdown")
    async def download_document_markdown(run_id: str, document_id: str) -> FileResponse:
        row = RunRepository(settings).get_generated_document(run_id, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        markdown_path = _safe_markdown_path(settings, row.get("markdown_path"))
        if markdown_path is None or not markdown_path.exists():
            raise HTTPException(status_code=404, detail="markdown artifact not found")
        return FileResponse(
            markdown_path,
            media_type="text/markdown; charset=utf-8",
            filename=f"{document_id}.md",
        )

    @app.get("/api/v1/legal-agent/runs/{run_id}/documents/{document_id}/docx")
    async def download_document_docx(run_id: str, document_id: str) -> FileResponse:
        row = RunRepository(settings).get_generated_document(run_id, document_id)
        if row is None:
            raise HTTPException(status_code=404, detail="document not found")
        docx_path = _safe_markdown_path(settings, row.get("docx_path"))
        if docx_path is None or not docx_path.exists():
            raise HTTPException(status_code=404, detail="docx artifact not found")
        return FileResponse(
            docx_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"{document_id}.docx",
        )

    return app


def _replay_timeline(audit: dict[str, object]) -> list[dict[str, object]]:
    events = audit.get("events") or []
    return [
        {
            "seq": event.get("seq"),
            "type": event.get("type"),
            "step_id": event.get("step_id"),
            "agent_role": event.get("agent_role"),
            "state_version": event.get("state_version"),
            "timestamp": event.get("timestamp"),
            "payload_hash": event.get("payload_hash"),
            "payload_ref": event.get("payload_ref"),
        }
        for event in events
        if isinstance(event, dict)
    ]


def _tool_replay(audit: dict[str, object]) -> list[dict[str, object]]:
    tool_ledger = audit.get("tool_ledger") or []
    return [
        {
            "tool_name": row.get("tool_name"),
            "tool_version": row.get("tool_version"),
            "status": row.get("status"),
            "step_id": row.get("step_id"),
            "tool_call_id": row.get("tool_call_id"),
            "idempotency_key": row.get("idempotency_key"),
            "request_ref": row.get("request_ref"),
            "response_ref": row.get("response_ref"),
            "error_type": row.get("error_type"),
        }
        for row in tool_ledger
        if isinstance(row, dict)
    ]


def _approval_replay(audit: dict[str, object]) -> list[dict[str, object]]:
    approvals = audit.get("approvals") or []
    return [
        {
            "approval_id": row.get("approval_id"),
            "approval_key": row.get("approval_key"),
            "tool_name": row.get("tool_name"),
            "risk_level": row.get("risk_level"),
            "status": row.get("status"),
            "requested_by": row.get("requested_by"),
            "approved_by": row.get("approved_by"),
            "decision_reason": row.get("decision_reason"),
            "request_ref": row.get("request_ref"),
        }
        for row in approvals
        if isinstance(row, dict)
    ]


def _artifact_replay(audit: dict[str, object]) -> list[dict[str, object]]:
    artifacts = audit.get("artifacts") or []
    return [
        {
            "artifact_id": row.get("artifact_id"),
            "name": row.get("name"),
            "step_id": row.get("step_id"),
            "blob_hash": row.get("blob_hash"),
            "blob_ref": row.get("blob_ref"),
            "metadata": row.get("metadata_json") or {},
        }
        for row in artifacts
        if isinstance(row, dict)
    ]


def _prometheus_metrics(settings: Settings, data: dict[str, object]) -> str:
    lines: list[str] = [
        "# HELP legal_agent_info Static legal agent service information.",
        "# TYPE legal_agent_info gauge",
    ]
    _metric(
        lines,
        "legal_agent_info",
        {
            "env": settings.env,
            "temporal_namespace": settings.temporal_namespace,
            "temporal_task_queue": settings.temporal_task_queue,
            "temporal_rag_task_queue": settings.temporal_rag_task_queue,
            "temporal_embedding_task_queue": settings.temporal_embedding_task_queue,
        },
        1,
    )

    lines.extend(
        [
            "# HELP legal_agent_runs_total Business runs by status and task type.",
            "# TYPE legal_agent_runs_total gauge",
        ]
    )
    for row in _list_metric_rows(data.get("business_run_status")):
        _metric(lines, "legal_agent_runs_total", {"status": row.get("run_status"), "task_type": row.get("task_type")}, row.get("count"))

    lines.extend(
        [
            "# HELP legal_agent_current_nodes Business runs by current node and node status.",
            "# TYPE legal_agent_current_nodes gauge",
        ]
    )
    for row in _list_metric_rows(data.get("current_node_status")):
        _metric(lines, "legal_agent_current_nodes", {"node": row.get("current_node"), "status": row.get("current_node_status")}, row.get("count"))

    lines.extend(
        [
            "# HELP legal_agent_approvals_total Business approval requests by status and risk level.",
            "# TYPE legal_agent_approvals_total gauge",
        ]
    )
    for row in _list_metric_rows(data.get("approval_status")):
        _metric(lines, "legal_agent_approvals_total", {"status": row.get("status"), "risk_level": row.get("risk_level")}, row.get("count"))

    lines.extend(
        [
            "# HELP legal_agent_idempotency_keys_total API idempotency keys by state.",
            "# TYPE legal_agent_idempotency_keys_total gauge",
        ]
    )
    for row in _list_metric_rows(data.get("idempotency_state")):
        _metric(lines, "legal_agent_idempotency_keys_total", {"state": row.get("state")}, row.get("count"))

    totals = data.get("business_totals") if isinstance(data.get("business_totals"), dict) else {}
    for name, key, help_text in [
        ("legal_agent_uploaded_files_total", "uploaded_files", "Uploaded files stored by the legal agent."),
        ("legal_agent_uploaded_file_chunks_total", "uploaded_file_chunks", "Uploaded file chunks indexed by the legal agent."),
        ("legal_agent_retrieval_evidence_total", "retrieval_evidence", "Retrieval evidence rows stored by legal agent runs."),
        ("legal_agent_generated_documents_total", "generated_documents", "Generated legal documents stored by the legal agent."),
    ]:
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} gauge"])
        _metric(lines, name, {}, totals.get(key) if isinstance(totals, dict) else 0)

    lines.extend(["# HELP rag_source_documents_total RAG source documents by type and status.", "# TYPE rag_source_documents_total gauge"])
    for row in _list_metric_rows(data.get("rag_source_documents")):
        _metric(lines, "rag_source_documents_total", {"doc_type": row.get("doc_type"), "status": row.get("status")}, row.get("count"))

    lines.extend(["# HELP rag_document_chunks_total RAG document chunks by type and authority level.", "# TYPE rag_document_chunks_total gauge"])
    for row in _list_metric_rows(data.get("rag_chunks")):
        _metric(lines, "rag_document_chunks_total", {"doc_type": row.get("doc_type"), "authority_level": row.get("authority_level")}, row.get("count"))

    latest_ingest = data.get("latest_ingest") if isinstance(data.get("latest_ingest"), dict) else {}
    if latest_ingest:
        lines.extend(["# HELP rag_latest_ingest_info Latest RAG ingest run information.", "# TYPE rag_latest_ingest_info gauge"])
        _metric(
            lines,
            "rag_latest_ingest_info",
            {
                "ingest_id": latest_ingest.get("ingest_id"),
                "domain": latest_ingest.get("domain"),
                "status": latest_ingest.get("status"),
            },
            1,
        )
        lines.extend(["# HELP rag_latest_ingest_finished_at_seconds Latest RAG ingest finish timestamp.", "# TYPE rag_latest_ingest_finished_at_seconds gauge"])
        _metric(lines, "rag_latest_ingest_finished_at_seconds", {}, _datetime_seconds(latest_ingest.get("finished_at")))

    agentledger = data.get("agentledger") if isinstance(data.get("agentledger"), dict) else {}
    lines.extend(["# HELP agentledger_tables_ready Whether AgentLedger PostgreSQL tables are present.", "# TYPE agentledger_tables_ready gauge"])
    _metric(lines, "agentledger_tables_ready", {}, 1 if agentledger.get("tables_ready") else 0)

    lines.extend(["# HELP agentledger_runs_total AgentLedger runs by status.", "# TYPE agentledger_runs_total gauge"])
    for row in _list_metric_rows(agentledger.get("runs")):
        _metric(lines, "agentledger_runs_total", {"status": row.get("status")}, row.get("count"))

    lines.extend(["# HELP agentledger_tool_ledger_total AgentLedger tool ledger rows by tool and status.", "# TYPE agentledger_tool_ledger_total gauge"])
    for row in _list_metric_rows(agentledger.get("tool_ledger")):
        _metric(lines, "agentledger_tool_ledger_total", {"tool_name": row.get("tool_name"), "status": row.get("status")}, row.get("count"))

    lines.extend(["# HELP agentledger_artifacts_total AgentLedger artifacts by kind.", "# TYPE agentledger_artifacts_total gauge"])
    for row in _list_metric_rows(agentledger.get("artifacts")):
        _metric(lines, "agentledger_artifacts_total", {"kind": row.get("kind")}, row.get("count"))

    lines.extend(["# HELP agentledger_approvals_total AgentLedger approvals by status and risk level.", "# TYPE agentledger_approvals_total gauge"])
    for row in _list_metric_rows(agentledger.get("approvals")):
        _metric(lines, "agentledger_approvals_total", {"status": row.get("status"), "risk_level": row.get("risk_level")}, row.get("count"))

    lines.extend(["# HELP agentledger_events_total AgentLedger event rows.", "# TYPE agentledger_events_total gauge"])
    _metric(lines, "agentledger_events_total", {}, agentledger.get("event_count") or 0)

    lines.extend(["# HELP agentledger_cost_records_total AgentLedger cost rows by category and name.", "# TYPE agentledger_cost_records_total gauge"])
    lines.extend(["# HELP agentledger_cost_amount_total AgentLedger summed cost amount by category and name.", "# TYPE agentledger_cost_amount_total gauge"])
    for row in _list_metric_rows(agentledger.get("cost_records")):
        labels = {"category": row.get("category"), "name": row.get("name")}
        _metric(lines, "agentledger_cost_records_total", labels, row.get("count"))
        _metric(lines, "agentledger_cost_amount_total", labels, row.get("amount"))

    return "\n".join(lines) + "\n"


def _list_metric_rows(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def _metric(lines: list[str], name: str, labels: dict[str, object], value: object) -> None:
    numeric = _number(value)
    if labels:
        label_text = ",".join(f'{key}="{_label_value(raw)}"' for key, raw in sorted(labels.items()))
        lines.append(f"{name}{{{label_text}}} {numeric}")
    else:
        lines.append(f"{name} {numeric}")


def _label_value(value: object) -> str:
    raw = "" if value is None else str(value)
    return raw.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _number(value: object) -> str:
    if value is None:
        return "0"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    try:
        return str(float(str(value)))
    except (TypeError, ValueError):
        return "0"


def _datetime_seconds(value: object) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return 0.0


def _progress(current_node: str | None) -> int:
    order = [
        "CLASSIFY",
        "FACT_CHECK",
        "ASK_USER",
        "PLAN",
        "RETRIEVE",
        "TOOL",
        "DRAFT",
        "REVIEW",
        "APPROVAL",
        "OUTPUT",
    ]
    if current_node not in order:
        return 0
    return int((order.index(current_node) + 1) / len(order) * 100)


app = create_app()


def _safe_markdown_path(settings: Settings, raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path).resolve()
    data_dir = settings.data_dir.resolve()
    try:
        path.relative_to(data_dir)
    except ValueError:
        return None
    return path
