from __future__ import annotations

from typing import Any

from temporalio.api.enums.v1 import common_pb2
from temporalio.api.operatorservice.v1 import request_response_pb2 as operator_pb2
from temporalio.client import Client
from temporalio.common import SearchAttributeKey, SearchAttributePair, SearchAttributeUpdate, TypedSearchAttributes

from legal_agent.core.config import Settings


TENANT_ID = SearchAttributeKey.for_keyword("LegalAgentTenantId")
USER_ID = SearchAttributeKey.for_keyword("LegalAgentUserId")
LEGAL_DOMAIN = SearchAttributeKey.for_keyword("LegalAgentLegalDomain")
RUN_STATUS = SearchAttributeKey.for_keyword("LegalAgentRunStatus")
RISK_LEVEL = SearchAttributeKey.for_keyword("LegalAgentRiskLevel")

REQUIRED_TEMPORAL_SEARCH_ATTRIBUTES: dict[str, int] = {
    TENANT_ID.name: common_pb2.INDEXED_VALUE_TYPE_KEYWORD,
    USER_ID.name: common_pb2.INDEXED_VALUE_TYPE_KEYWORD,
    LEGAL_DOMAIN.name: common_pb2.INDEXED_VALUE_TYPE_KEYWORD,
    RUN_STATUS.name: common_pb2.INDEXED_VALUE_TYPE_KEYWORD,
    RISK_LEVEL.name: common_pb2.INDEXED_VALUE_TYPE_KEYWORD,
}


def temporal_run_memo(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "legal_agent_run_id": payload.get("run_id"),
        "agentledger_run_id": payload.get("agentledger_run_id"),
        "request_id": payload.get("request_id"),
        "tenant_id": payload.get("tenant_id"),
        "user_id": payload.get("user_id"),
        "task_type": payload.get("task_type"),
        "legal_domain": payload.get("legal_domain"),
        "jurisdiction": payload.get("jurisdiction"),
        "risk_level": payload.get("risk_level"),
    }


def temporal_search_attributes(payload: dict[str, Any]) -> TypedSearchAttributes:
    values = _attribute_values(payload)
    return TypedSearchAttributes(
        [
            SearchAttributePair(TENANT_ID, values["tenant_id"]),
            SearchAttributePair(USER_ID, values["user_id"]),
            SearchAttributePair(LEGAL_DOMAIN, values["legal_domain"]),
            SearchAttributePair(RUN_STATUS, values["run_status"]),
            SearchAttributePair(RISK_LEVEL, values["risk_level"]),
        ]
    )


def temporal_search_attribute_updates(payload: dict[str, Any], *, run_status: str | None = None) -> list[SearchAttributeUpdate[Any]]:
    values = _attribute_values({**payload, **({"run_status": run_status} if run_status else {})})
    return [
        TENANT_ID.value_set(values["tenant_id"]),
        USER_ID.value_set(values["user_id"]),
        LEGAL_DOMAIN.value_set(values["legal_domain"]),
        RUN_STATUS.value_set(values["run_status"]),
        RISK_LEVEL.value_set(values["risk_level"]),
    ]


async def ensure_temporal_search_attributes(settings: Settings, client: Client) -> dict[str, Any]:
    if not settings.temporal_search_attributes_enabled:
        return {
            "enabled": False,
            "registered": False,
            "required_attributes": REQUIRED_TEMPORAL_SEARCH_ATTRIBUTES,
            "missing_attributes": [],
        }
    status = await temporal_search_attribute_status(settings, client)
    missing = dict(status["missing_attributes"])
    if missing:
        request = operator_pb2.AddSearchAttributesRequest(namespace=settings.temporal_namespace)
        request.search_attributes.update(missing)
        try:
            await client.operator_service.add_search_attributes(request)
        except Exception as exc:
            refreshed = await temporal_search_attribute_status(settings, client)
            if refreshed["missing_attributes"]:
                return {
                    **refreshed,
                    "registered": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return {**refreshed, "registered": True}
    refreshed = await temporal_search_attribute_status(settings, client)
    return {**refreshed, "registered": not bool(refreshed["missing_attributes"])}


async def temporal_search_attribute_status(settings: Settings, client: Client) -> dict[str, Any]:
    if not settings.temporal_search_attributes_enabled:
        return {
            "enabled": False,
            "registered": False,
            "required_attributes": REQUIRED_TEMPORAL_SEARCH_ATTRIBUTES,
            "existing_attributes": {},
            "missing_attributes": {},
        }
    response = await client.operator_service.list_search_attributes(
        operator_pb2.ListSearchAttributesRequest(namespace=settings.temporal_namespace)
    )
    existing = {**dict(response.system_attributes), **dict(response.custom_attributes)}
    missing = {
        name: value
        for name, value in REQUIRED_TEMPORAL_SEARCH_ATTRIBUTES.items()
        if existing.get(name) != value
    }
    return {
        "enabled": True,
        "registered": not bool(missing),
        "namespace": settings.temporal_namespace,
        "required_attributes": REQUIRED_TEMPORAL_SEARCH_ATTRIBUTES,
        "existing_attributes": {name: existing.get(name) for name in REQUIRED_TEMPORAL_SEARCH_ATTRIBUTES},
        "missing_attributes": missing,
    }


def _attribute_values(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "tenant_id": str(payload.get("tenant_id") or "default"),
        "user_id": str(payload.get("user_id") or "demo-user"),
        "legal_domain": str(payload.get("legal_domain") or "unknown"),
        "run_status": str(payload.get("run_status") or "CREATED"),
        "risk_level": str(payload.get("risk_level") or "L2"),
    }
