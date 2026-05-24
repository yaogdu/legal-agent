from __future__ import annotations

import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from legal_agent.core.config import Settings, load_settings
from legal_agent.workflows.activities import (
    approval_activity,
    approval_decision_activity,
    approval_timeout_activity,
    classify_activity,
    draft_activity,
    embedding_backfill_activity,
    fact_check_activity,
    health_check_activity,
    output_activity,
    plan_activity,
    retrieve_activity,
    review_activity,
    tool_activity,
    user_input_timeout_activity,
)
from legal_agent.workflows.legal_agent import EmbeddingBackfillWorkflow, HealthCheckWorkflow, LegalAgentWorkflow


async def run_worker(settings: Settings, *, kind: str = "agent") -> None:
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    if kind == "rag":
        task_queue = settings.temporal_rag_task_queue
        workflows = [HealthCheckWorkflow]
        activities = [health_check_activity, retrieve_activity]
    elif kind == "embedding":
        task_queue = settings.temporal_embedding_task_queue
        workflows = [HealthCheckWorkflow, EmbeddingBackfillWorkflow]
        activities = [health_check_activity, embedding_backfill_activity]
    else:
        task_queue = settings.temporal_task_queue
        workflows = [LegalAgentWorkflow, HealthCheckWorkflow]
        activities = [
            classify_activity,
            fact_check_activity,
            user_input_timeout_activity,
            health_check_activity,
            plan_activity,
            retrieve_activity,
            tool_activity,
            draft_activity,
            review_activity,
            approval_activity,
            approval_decision_activity,
            approval_timeout_activity,
            output_activity,
        ]
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=workflows,
        activities=activities,
    )
    await worker.run()


def main() -> None:
    asyncio.run(run_worker(load_settings()))
