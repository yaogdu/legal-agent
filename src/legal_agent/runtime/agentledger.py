from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from legal_agent.core.config import Settings
from legal_agent.tools.case_search import CASE_SEARCH_INPUT_SCHEMA, CASE_SEARCH_OUTPUT_SCHEMA, case_from_row
from legal_agent.tools.citation_checker import CITATION_CHECKER_INPUT_SCHEMA, CITATION_CHECKER_OUTPUT_SCHEMA, citation_checker
from legal_agent.tools.document_template import DOCUMENT_TEMPLATE_INPUT_SCHEMA, DOCUMENT_TEMPLATE_OUTPUT_SCHEMA, document_template_tool
from legal_agent.tools.format_checker import (
    FORMAT_CHECKER_INPUT_SCHEMA,
    FORMAT_CHECKER_OUTPUT_SCHEMA,
    format_checker,
)
from legal_agent.tools.salary_calculator import (
    SALARY_CALCULATOR_INPUT_SCHEMA,
    SALARY_CALCULATOR_OUTPUT_SCHEMA,
    salary_calculator,
)


def create_runtime(settings: Settings) -> Any:
    from agentledger import LocalBlobStore, PostgresStore, PostgresStoreConfig, Runtime

    settings.agentledger_blob_dir.mkdir(parents=True, exist_ok=True)
    store = PostgresStore(
        PostgresStoreConfig(
            dsn=settings.agentledger_postgres_dsn or settings.database_dsn,
            schema=settings.agentledger_postgres_schema,
        )
    )
    store.init()
    return Runtime(store=store, blobs=LocalBlobStore(settings.agentledger_blob_dir))


def _inspector_redaction_policy() -> Any:
    from agentledger_inspector import InspectorRedactionPolicy

    return InspectorRedactionPolicy(
        keys=(
            "authorization",
            "api_key",
            "apikey",
            "token",
            "password",
            "secret",
            "llm_api_key",
            "langfuse_secret_key",
        )
    )


def build_agentledger_inspector_report(settings: Settings, *, agentledger_run_id: str, include_payloads: bool = False) -> dict[str, Any]:
    from agentledger_inspector import InspectorDataSource

    report = InspectorDataSource().from_postgres(
        dsn=settings.agentledger_postgres_dsn or settings.database_dsn,
        schema=settings.agentledger_postgres_schema,
        blob_root=settings.agentledger_blob_dir,
        run_id=agentledger_run_id,
        include_payloads=include_payloads,
        redaction_policy=_inspector_redaction_policy(),
    )
    return dict(report.to_dict())


def build_agentledger_inspector_html(settings: Settings, *, agentledger_run_id: str, include_payloads: bool = False) -> str:
    from agentledger_inspector import InspectorDataSource

    report = InspectorDataSource().from_postgres(
        dsn=settings.agentledger_postgres_dsn or settings.database_dsn,
        schema=settings.agentledger_postgres_schema,
        blob_root=settings.agentledger_blob_dir,
        run_id=agentledger_run_id,
        include_payloads=include_payloads,
        redaction_policy=_inspector_redaction_policy(),
    )
    return str(report.to_html())


def build_agentledger_inspector_run_index(
    settings: Settings,
    *,
    limit: int = 100,
    status: str | None = None,
    run_link_template: str | None = None,
) -> dict[str, Any]:
    from agentledger_inspector import InspectorDataSource

    index = InspectorDataSource().runs_from_postgres(
        dsn=settings.agentledger_postgres_dsn or settings.database_dsn,
        schema=settings.agentledger_postgres_schema,
        blob_root=settings.agentledger_blob_dir,
        limit=limit,
        status=status,
        run_link_template=run_link_template,
    )
    return dict(index.to_dict())


def build_agentledger_inspector_run_index_html(
    settings: Settings,
    *,
    limit: int = 100,
    status: str | None = None,
    run_link_template: str | None = None,
) -> str:
    from agentledger_inspector import InspectorDataSource

    index = InspectorDataSource().runs_from_postgres(
        dsn=settings.agentledger_postgres_dsn or settings.database_dsn,
        schema=settings.agentledger_postgres_schema,
        blob_root=settings.agentledger_blob_dir,
        limit=limit,
        status=status,
        run_link_template=run_link_template,
    )
    return str(index.to_html())


def _gateway_context(runtime: Any, *, agentledger_run_id: str, step_id: str) -> SimpleNamespace:
    _, state_version, session_id = runtime.store.load_state(agentledger_run_id)
    return SimpleNamespace(
        run_id=agentledger_run_id,
        session_id=session_id,
        step_id=step_id,
        agent_role="legal-agent",
        lease_token="temporal-managed",
        attempt=1,
        state_version=state_version,
        execution_mode="normal",
        source_run_id=None,
    )


async def call_salary_calculator_tool(settings: Settings, *, agentledger_run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    runtime = create_runtime(settings)
    try:
        from agentledger import tool

        runtime.register_tool(
            tool(
                name="salary_calculator",
                description="Calculate labor-dispute monetary claim estimates.",
                side_effect="none",
                risk_level="L2",
                idempotency=True,
                input_schema=SALARY_CALCULATOR_INPUT_SCHEMA,
                output_schema=SALARY_CALCULATOR_OUTPUT_SCHEMA,
                version="v1",
            )(salary_calculator)
        )
        ctx = _gateway_context(runtime, agentledger_run_id=agentledger_run_id, step_id="legal-agent:tool:salary_calculator")
        result = await runtime.gateway.call(ctx, "salary_calculator", args)
        return dict(result)
    finally:
        runtime.close()


async def call_format_checker_tool(settings: Settings, *, agentledger_run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    runtime = create_runtime(settings)
    try:
        from agentledger import tool

        runtime.register_tool(
            tool(
                name="format_checker",
                description="Check required Markdown sections for legal documents.",
                side_effect="none",
                risk_level="L1",
                idempotency=True,
                input_schema=FORMAT_CHECKER_INPUT_SCHEMA,
                output_schema=FORMAT_CHECKER_OUTPUT_SCHEMA,
                version="v1",
            )(format_checker)
        )
        ctx = _gateway_context(runtime, agentledger_run_id=agentledger_run_id, step_id="legal-agent:tool:format_checker")
        result = await runtime.gateway.call(ctx, "format_checker", args)
        return dict(result)
    finally:
        runtime.close()


async def call_case_search_tool(settings: Settings, *, agentledger_run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    runtime = create_runtime(settings)
    try:
        from agentledger import tool
        from legal_agent.db.repository import RunRepository

        def case_search_api(tool_args: dict[str, Any]) -> dict[str, Any]:
            query = str(tool_args.get("query") or "")
            jurisdiction = str(tool_args.get("jurisdiction") or "CN")
            top_k = int(tool_args.get("top_k") or 5)
            rows = RunRepository(settings).search_case_chunks(query=query, jurisdiction=jurisdiction, limit=top_k)
            return {
                "status": "ok",
                "query": query,
                "jurisdiction": jurisdiction,
                "cases": [case_from_row(dict(row)) for row in rows],
            }

        runtime.register_tool(
            tool(
                name="case_search_api",
                description="Search local labor-dispute case references from the RAG corpus.",
                side_effect="none",
                risk_level="L1",
                idempotency=True,
                input_schema=CASE_SEARCH_INPUT_SCHEMA,
                output_schema=CASE_SEARCH_OUTPUT_SCHEMA,
                version="v1",
            )(case_search_api)
        )
        ctx = _gateway_context(runtime, agentledger_run_id=agentledger_run_id, step_id="legal-agent:tool:case_search_api")
        result = await runtime.gateway.call(ctx, "case_search_api", args)
        return dict(result)
    finally:
        runtime.close()


async def call_citation_checker_tool(settings: Settings, *, agentledger_run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    runtime = create_runtime(settings)
    try:
        from agentledger import tool

        runtime.register_tool(
            tool(
                name="citation_checker",
                description="Check that legal basis citations are grounded in the evidence pack.",
                side_effect="none",
                risk_level="L1",
                idempotency=True,
                input_schema=CITATION_CHECKER_INPUT_SCHEMA,
                output_schema=CITATION_CHECKER_OUTPUT_SCHEMA,
                version="v1",
            )(citation_checker)
        )
        ctx = _gateway_context(runtime, agentledger_run_id=agentledger_run_id, step_id="legal-agent:tool:citation_checker")
        result = await runtime.gateway.call(ctx, "citation_checker", args)
        return dict(result)
    finally:
        runtime.close()


async def call_document_template_tool(settings: Settings, *, agentledger_run_id: str, args: dict[str, Any]) -> dict[str, Any]:
    runtime = create_runtime(settings)
    try:
        from agentledger import tool

        runtime.register_tool(
            tool(
                name="document_template_tool",
                description="Render local legal document templates for the current task.",
                side_effect="none",
                risk_level="L1",
                idempotency=True,
                input_schema=DOCUMENT_TEMPLATE_INPUT_SCHEMA,
                output_schema=DOCUMENT_TEMPLATE_OUTPUT_SCHEMA,
                version="v1",
            )(document_template_tool)
        )
        ctx = _gateway_context(runtime, agentledger_run_id=agentledger_run_id, step_id="legal-agent:tool:document_template_tool")
        result = await runtime.gateway.call(ctx, "document_template_tool", args)
        return dict(result)
    finally:
        runtime.close()


def create_agentledger_run(settings: Settings, initial_state: dict[str, Any]) -> str:
    runtime = create_runtime(settings)
    try:
        run_id, _ = runtime.create_run(initial_state=initial_state)
        return run_id
    finally:
        runtime.close()


def patch_agentledger_state(settings: Settings, agentledger_run_id: str, patch: dict[str, Any], reason: str) -> None:
    runtime = create_runtime(settings)
    try:
        runtime.store.apply_system_state_patch(run_id=agentledger_run_id, patch=patch, reason=reason)
    finally:
        runtime.close()


def create_agentledger_artifact(
    settings: Settings,
    *,
    agentledger_run_id: str,
    name: str,
    value: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    runtime = create_runtime(settings)
    try:
        blob_hash, blob_ref = runtime.blobs.put_json(value)
        artifact_id = runtime.store.create_artifact(
            run_id=agentledger_run_id,
            step_id=None,
            name=name,
            blob_hash=blob_hash,
            blob_ref=blob_ref,
            metadata=metadata or {},
        )
        return {"artifact_id": artifact_id, "blob_hash": blob_hash, "blob_ref": blob_ref}
    finally:
        runtime.close()


def request_agentledger_approval(
    settings: Settings,
    *,
    agentledger_run_id: str,
    approval_key: str,
    request: dict[str, Any],
    tool_name: str,
    risk_level: str,
    reason: str,
    requested_by: str = "legal-agent",
) -> dict[str, Any]:
    runtime = create_runtime(settings)
    try:
        request_hash, request_ref = runtime.blobs.put_json(request)
        row = runtime.store.request_approval(
            approval_key=approval_key,
            run_id=agentledger_run_id,
            session_id=None,
            step_id="legal-agent:approval",
            tool_name=tool_name,
            risk_level=risk_level,
            reason=reason,
            request_hash=request_hash,
            request_ref=request_ref,
            requested_by=requested_by,
        )
        return dict(row)
    finally:
        runtime.close()


def decide_agentledger_approval(
    settings: Settings,
    *,
    agentledger_approval_id: str,
    approved: bool,
    approver: str,
    reason: str,
) -> dict[str, Any]:
    runtime = create_runtime(settings)
    try:
        if approved:
            row = runtime.store.approve_request(agentledger_approval_id, approver=approver, reason=reason)
        else:
            row = runtime.store.deny_request(agentledger_approval_id, approver=approver, reason=reason)
        return dict(row)
    finally:
        runtime.close()
