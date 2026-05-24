from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import uuid4

from temporalio.client import Client, WorkflowFailureError

from legal_agent.core.config import Settings
from legal_agent.workflows.search_attributes import ensure_temporal_search_attributes, temporal_run_memo, temporal_search_attributes
from legal_agent.workflows.legal_agent import EmbeddingBackfillWorkflow, HealthCheckWorkflow, LegalAgentWorkflow


async def temporal_client(settings: Settings) -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


def workflow_id_for_run(run_id: str) -> str:
    return f"legal-agent:{run_id}"


async def start_legal_workflow(settings: Settings, payload: dict[str, Any]) -> str:
    client = await temporal_client(settings)
    workflow_payload = {**payload}
    memo = temporal_run_memo(workflow_payload)
    workflow_id = payload["temporal_workflow_id"]
    search_attribute_status = await ensure_temporal_search_attributes(settings, client)
    if search_attribute_status.get("registered"):
        workflow_payload["temporal_search_attributes_enabled"] = True
        try:
            await client.start_workflow(
                LegalAgentWorkflow.run,
                workflow_payload,
                id=workflow_id,
                task_queue=settings.temporal_task_queue,
                memo=memo,
                search_attributes=temporal_search_attributes(workflow_payload),
            )
            return workflow_id
        except Exception as exc:
            message = str(exc).lower()
            if "search attribute" not in message and "searchattributes" not in message:
                raise
    workflow_payload["temporal_search_attributes_enabled"] = False
    await client.start_workflow(
        LegalAgentWorkflow.run,
        workflow_payload,
        id=workflow_id,
        task_queue=settings.temporal_task_queue,
        memo=memo,
    )
    return workflow_id


async def run_health_check_workflow(settings: Settings, *, task_queue: str | None = None, worker: str = "legal-agent-worker") -> dict[str, Any]:
    client = await temporal_client(settings)
    queue = task_queue or settings.temporal_task_queue
    return await client.execute_workflow(
        HealthCheckWorkflow.run,
        {"task_queue": queue, "worker": worker},
        id=f"legal-agent-health:{uuid4()}",
        task_queue=queue,
        execution_timeout=timedelta(seconds=10),
    )


async def run_embedding_backfill_workflow(settings: Settings, *, limit: int = 100) -> dict[str, Any]:
    client = await temporal_client(settings)
    return await client.execute_workflow(
        EmbeddingBackfillWorkflow.run,
        {"limit": limit, "task_queue": settings.temporal_embedding_task_queue},
        id=f"legal-agent-embedding-backfill:{uuid4()}",
        task_queue=settings.temporal_embedding_task_queue,
        execution_timeout=timedelta(seconds=120),
    )


async def signal_facts(settings: Settings, run_id: str, facts: dict[str, Any]) -> None:
    client = await temporal_client(settings)
    handle = client.get_workflow_handle(workflow_id_for_run(run_id))
    try:
        await handle.signal(LegalAgentWorkflow.submit_facts, facts)
    except WorkflowFailureError:
        raise


async def signal_approval(settings: Settings, run_id: str, decision: dict[str, Any]) -> None:
    client = await temporal_client(settings)
    handle = client.get_workflow_handle(workflow_id_for_run(run_id))
    try:
        await handle.signal(LegalAgentWorkflow.submit_approval, decision)
    except WorkflowFailureError:
        raise


async def cancel_legal_workflow(settings: Settings, run_id: str) -> None:
    client = await temporal_client(settings)
    handle = client.get_workflow_handle(workflow_id_for_run(run_id))
    await handle.cancel()
