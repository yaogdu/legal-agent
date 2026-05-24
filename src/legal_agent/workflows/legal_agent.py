from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
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
    from legal_agent.workflows.search_attributes import temporal_search_attribute_updates


def _upsert_temporal_run_status(payload: dict[str, Any], run_status: str) -> None:
    if not payload.get("temporal_search_attributes_enabled"):
        return
    workflow.upsert_search_attributes(temporal_search_attribute_updates(payload, run_status=run_status))
    payload["run_status"] = run_status


@workflow.defn
class LegalAgentWorkflow:
    def __init__(self) -> None:
        self._facts_signal: dict[str, Any] | None = None
        self._approval_signal: dict[str, Any] | None = None

    @workflow.signal
    async def submit_facts(self, facts: dict[str, Any]) -> None:
        self._facts_signal = facts

    @workflow.signal
    async def submit_approval(self, decision: dict[str, Any]) -> None:
        self._approval_signal = decision

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        retry_policy = RetryPolicy(maximum_attempts=3)
        activity_timeout = timedelta(seconds=60)

        payload = await workflow.execute_activity(
            classify_activity,
            payload,
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        _upsert_temporal_run_status(payload, "RUNNING")
        payload = await workflow.execute_activity(
            fact_check_activity,
            payload,
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        while not payload.get("can_continue", True):
            _upsert_temporal_run_status(payload, "WAITING_USER_INPUT")
            user_input_timeout_seconds = int(payload.get("user_input_timeout_seconds") or 86400)
            try:
                await workflow.wait_condition(
                    lambda: self._facts_signal is not None,
                    timeout=timedelta(seconds=user_input_timeout_seconds),
                    timeout_summary=f"user-input-timeout:{payload.get('run_id')}",
                )
            except asyncio.TimeoutError:
                payload = await workflow.execute_activity(
                    user_input_timeout_activity,
                    payload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=retry_policy,
                )
                _upsert_temporal_run_status(payload, "EXPIRED")
                return payload.get("result", {})
            payload["signal_facts"] = self._facts_signal
            self._facts_signal = None
            payload = await workflow.execute_activity(
                fact_check_activity,
                payload,
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
            if payload.get("can_continue", True):
                _upsert_temporal_run_status(payload, "RUNNING")
        payload = await workflow.execute_activity(
            plan_activity,
            payload,
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        retrieve_kwargs: dict[str, Any] = {}
        if payload.get("rag_task_queue"):
            retrieve_kwargs["task_queue"] = str(payload["rag_task_queue"])
        payload = await workflow.execute_activity(
            retrieve_activity,
            payload,
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
            **retrieve_kwargs,
        )
        for act in (tool_activity, draft_activity, review_activity, approval_activity):
            payload = await workflow.execute_activity(
                act,
                payload,
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
        while not payload.get("can_continue", True):
            _upsert_temporal_run_status(payload, "WAITING_APPROVAL")
            approval_timeout_seconds = int(payload.get("approval_timeout_seconds") or 86400)
            try:
                await workflow.wait_condition(
                    lambda: self._approval_signal is not None,
                    timeout=timedelta(seconds=approval_timeout_seconds),
                    timeout_summary=f"approval-timeout:{payload.get('run_id')}",
                )
            except asyncio.TimeoutError:
                payload = await workflow.execute_activity(
                    approval_timeout_activity,
                    payload,
                    start_to_close_timeout=activity_timeout,
                    retry_policy=retry_policy,
                )
                _upsert_temporal_run_status(payload, "EXPIRED")
                return payload.get("result", {})
            payload["approval_signal"] = self._approval_signal
            self._approval_signal = None
            payload = await workflow.execute_activity(
                approval_decision_activity,
                payload,
                start_to_close_timeout=activity_timeout,
                retry_policy=retry_policy,
            )
            if payload.get("approval", {}).get("status") == "DENIED":
                _upsert_temporal_run_status(payload, "APPROVAL_REJECTED")
                return payload.get("result", {})
            _upsert_temporal_run_status(payload, "RUNNING")
        payload = await workflow.execute_activity(
            output_activity,
            payload,
            start_to_close_timeout=activity_timeout,
            retry_policy=retry_policy,
        )
        _upsert_temporal_run_status(payload, "COMPLETED")
        return payload.get("result", {})


@workflow.defn
class HealthCheckWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            health_check_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=5),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )


@workflow.defn
class EmbeddingBackfillWorkflow:
    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            embedding_backfill_activity,
            payload,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
