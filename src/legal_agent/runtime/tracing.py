from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

from legal_agent.core.config import Settings


@contextmanager
def trace_span(
    settings: Settings,
    name: str,
    metadata: dict[str, Any],
    *,
    as_type: str = "span",
    input: Any | None = None,
    output: Any | None = None,
    model: str | None = None,
    model_parameters: dict[str, Any] | None = None,
) -> Iterator[Any | None]:
    if not _langfuse_ready(settings):
        yield
        return
    client = _langfuse_client(settings)
    if client is None:
        yield
        return
    span = None
    try:
        span = client.start_observation(
            name=name,
            as_type=as_type,
            trace_context={"trace_id": _trace_id(metadata)},
            input=input,
            output=output,
            metadata=_metadata(settings, metadata),
            model=model,
            model_parameters=model_parameters,
        )
    except Exception:
        yield
        return
    try:
        yield span
        if output is None:
            span.update(output={"status": "ok"})
    except Exception as exc:
        try:
            span.update(output={"status": "error", "error": repr(exc)}, level="ERROR", status_message=repr(exc))
        finally:
            _finish(span, client)
        raise
    else:
        _finish(span, client)


def update_observation(
    observation: Any | None,
    *,
    output: Any | None = None,
    usage_details: dict[str, int] | None = None,
    level: str | None = None,
    status_message: str | None = None,
) -> None:
    if observation is None:
        return
    update: dict[str, Any] = {}
    if output is not None:
        update["output"] = output
    if usage_details:
        update["usage_details"] = usage_details
    if level:
        update["level"] = level
    if status_message:
        update["status_message"] = status_message
    if update:
        observation.update(**update)


def probe_langfuse(settings: Settings) -> dict[str, Any]:
    if not _langfuse_ready(settings):
        return {"enabled": settings.langfuse_enabled, "configured": False}
    client = _langfuse_client(settings)
    if client is None:
        return {"enabled": True, "configured": True, "auth_ok": False, "error": "langfuse client unavailable"}
    trace_id = _trace_id({"run_id": f"health:{uuid4().hex}"})
    auth_ok = bool(client.auth_check())
    if auth_ok:
        observation = client.start_observation(
            name="health.langfuse",
            as_type="span",
            trace_context={"trace_id": trace_id},
            input={"probe": "healthz.details"},
            metadata=_metadata(settings, {"run_id": f"health:{trace_id}", "node": "HEALTH"}),
        )
        observation.update(output={"status": "ok"})
        _finish(observation, client)
    return {
        "enabled": True,
        "configured": True,
        "auth_ok": auth_ok,
        "trace_id": trace_id if auth_ok else None,
        "trace_url": client.get_trace_url(trace_id=trace_id) if auth_ok else None,
    }


def _langfuse_ready(settings: Settings) -> bool:
    return bool(
        settings.langfuse_enabled
        and settings.langfuse_host
        and settings.langfuse_public_key
        and settings.langfuse_secret_key
    )


def _langfuse_client(settings: Settings) -> Any | None:
    try:
        os.environ["LANGFUSE_HOST"] = settings.langfuse_host
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.langfuse_public_key
        os.environ["LANGFUSE_SECRET_KEY"] = settings.langfuse_secret_key
        from langfuse import get_client

        return get_client()
    except Exception:
        return None


def _trace_id(metadata: dict[str, Any]) -> str:
    raw = str(metadata.get("run_id") or metadata.get("agentledger_run_id") or metadata.get("temporal_workflow_id") or "legal-agent")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _metadata(settings: Settings, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        **metadata,
        "project": settings.langfuse_project,
        "service": "legal-agent",
    }


def _finish(observation: Any, client: Any) -> None:
    try:
        observation.end()
    finally:
        try:
            client.flush()
        except Exception:
            pass
