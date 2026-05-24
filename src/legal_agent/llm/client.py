from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from legal_agent.core.config import Settings
from legal_agent.runtime.tracing import trace_span, update_observation


class LLMConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMGenerationResult:
    markdown: str | None
    enabled: bool
    provider: str
    model: str | None
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None

    def to_audit_payload(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "request_payload": self.request_payload,
            "response_payload": self.response_payload,
        }


@dataclass(frozen=True)
class LLMClaimExtractionResult:
    claims: list[dict[str, Any]]
    enabled: bool
    provider: str
    model: str | None
    request_payload: dict[str, Any] | None = None
    response_payload: dict[str, Any] | None = None
    error: str | None = None

    def to_audit_payload(self) -> dict[str, Any]:
        return {
            "claims": self.claims,
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
            "request_payload": self.request_payload,
            "response_payload": self.response_payload,
            "error": self.error,
        }


async def extract_labor_claims_result(
    settings: Settings,
    *,
    user_input: str,
    metadata: dict[str, Any] | None = None,
) -> LLMClaimExtractionResult:
    if not settings.llm_enabled:
        return LLMClaimExtractionResult(
            claims=[],
            enabled=False,
            provider=settings.llm_provider,
            model=settings.llm_model or None,
        )
    _ensure_openai_compatible_config(settings)
    payload = {
        "model": settings.llm_model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是劳动争议诉求抽取器。只输出 JSON，不要输出 Markdown。"
                    "从用户案情中抽取候选仲裁诉求。"
                    "已知诉求可使用标准 type：illegal_termination_damages, economic_compensation, unpaid_salary, double_salary, "
                    "year_end_bonus, overtime_pay, unused_annual_leave_pay, social_insurance。"
                    "如果用户提出其他诉求，不要丢弃；type 用简短英文或拼音标识，并在 label 中保留中文诉求名称。"
                    "保留用户明确表达的 requested 和 company_offer，例如 2N、N+1。"
                ),
            },
            {
                "role": "user",
                "content": (
                    "用户案情：\n"
                    f"{user_input}\n\n"
                    "输出格式：\n"
                    '{"claims":[{"type":"illegal_termination_damages","label":"违法解除赔偿金","requested":"2N","company_offer":"N+1","confidence":0.8},{"type":"stock_option_compensation","label":"期权补偿","confidence":0.7}]}'
                ),
            },
        ],
    }
    trace_metadata = {
        **(metadata or {}),
        "document_kind": "labor_claim_extraction",
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
    }
    try:
        with trace_span(
            settings,
            "llm.claim_extraction",
            trace_metadata,
            as_type="generation",
            input={"messages": payload["messages"], "temperature": payload["temperature"]},
            model=settings.llm_model,
            model_parameters={"temperature": 0, "provider": settings.llm_provider},
        ) as generation:
            async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                response = await client.post(
                    _chat_completions_url(settings.llm_base_url),
                    headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
            data = response.json()
            content = str(data["choices"][0]["message"]["content"]).strip()
            parsed = _parse_json_object(content)
            claims = parsed.get("claims") if isinstance(parsed, dict) else []
            update_observation(
                generation,
                output={
                    "id": data.get("id"),
                    "model": data.get("model"),
                    "finish_reason": data.get("choices", [{}])[0].get("finish_reason"),
                    "content": content,
                },
                usage_details=_usage_details(data.get("usage")),
            )
        return LLMClaimExtractionResult(
            claims=claims if isinstance(claims, list) else [],
            enabled=True,
            provider=settings.llm_provider,
            model=settings.llm_model,
            request_payload=payload,
            response_payload={
                "id": data.get("id"),
                "model": data.get("model"),
                "usage": data.get("usage"),
                "finish_reason": data.get("choices", [{}])[0].get("finish_reason"),
                "content": content,
            },
        )
    except Exception as exc:
        return LLMClaimExtractionResult(
            claims=[],
            enabled=True,
            provider=settings.llm_provider,
            model=settings.llm_model,
            request_payload=payload,
            error=repr(exc),
        )


async def generate_labor_arbitration_markdown(
    settings: Settings,
    *,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    result = await generate_labor_arbitration_markdown_result(
        settings,
        user_input=user_input,
        facts=facts,
        evidence_pack=evidence_pack,
        amount_calculation=amount_calculation,
        template_markdown=template_markdown,
        metadata=metadata,
    )
    return result.markdown


async def generate_labor_arbitration_markdown_result(
    settings: Settings,
    *,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> LLMGenerationResult:
    return await generate_legal_markdown_result(
        settings,
        document_kind="labor_arbitration_application",
        system_instruction=(
            "你是劳动争议法律文书生成助手。只输出 Markdown 草稿。"
            "必须保留模板中的栏目结构；不得编造事实、法律依据或证据；"
            "缺失字段用“待补充”；法律依据只能使用 evidence_pack 中的 citation_anchor。"
        ),
        user_input=user_input,
        facts=facts,
        evidence_pack=evidence_pack,
        amount_calculation=amount_calculation,
        template_markdown=template_markdown,
        metadata=metadata,
    )


async def generate_case_analysis_markdown(
    settings: Settings,
    *,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    result = await generate_case_analysis_markdown_result(
        settings,
        user_input=user_input,
        facts=facts,
        evidence_pack=evidence_pack,
        amount_calculation=amount_calculation,
        template_markdown=template_markdown,
        metadata=metadata,
    )
    return result.markdown


async def generate_case_analysis_markdown_result(
    settings: Settings,
    *,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> LLMGenerationResult:
    return await generate_legal_markdown_result(
        settings,
        document_kind="labor_dispute_case_analysis",
        system_instruction=(
            "你是劳动争议案情分析助手。只输出 Markdown 草稿。"
            "必须保留模板中的栏目结构；不得编造事实、法律依据、证据或类案；"
            "法律依据只能来自 source_type 为 law、judicial_interpretation、administrative_regulation、department_rule、regulation 的 evidence_pack；"
            "类案只能放入“类案参考”，不能作为法律依据；缺失字段用“待补充”。"
        ),
        user_input=user_input,
        facts=facts,
        evidence_pack=evidence_pack,
        amount_calculation=amount_calculation,
        template_markdown=template_markdown,
        metadata=metadata,
    )


async def generate_legal_markdown(
    settings: Settings,
    *,
    document_kind: str,
    system_instruction: str,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    result = await generate_legal_markdown_result(
        settings,
        document_kind=document_kind,
        system_instruction=system_instruction,
        user_input=user_input,
        facts=facts,
        evidence_pack=evidence_pack,
        amount_calculation=amount_calculation,
        template_markdown=template_markdown,
        metadata=metadata,
    )
    return result.markdown


async def generate_legal_markdown_result(
    settings: Settings,
    *,
    document_kind: str,
    system_instruction: str,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
    metadata: dict[str, Any] | None = None,
) -> LLMGenerationResult:
    if not settings.llm_enabled:
        return LLMGenerationResult(
            markdown=None,
            enabled=False,
            provider=settings.llm_provider,
            model=settings.llm_model or None,
        )
    _ensure_openai_compatible_config(settings)
    payload = _chat_completion_payload(
        settings,
        document_kind=document_kind,
        system_instruction=system_instruction,
        user_input=user_input,
        facts=facts,
        evidence_pack=evidence_pack,
        amount_calculation=amount_calculation,
        template_markdown=template_markdown,
    )
    trace_metadata = {
        **(metadata or {}),
        "document_kind": document_kind,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "evidence_count": len(evidence_pack),
    }
    with trace_span(
        settings,
        "llm.draft_generation",
        trace_metadata,
        as_type="generation",
        input=_langfuse_generation_input(document_kind=document_kind, payload=payload),
        model=settings.llm_model,
        model_parameters={
            "temperature": settings.llm_temperature,
            "provider": settings.llm_provider,
        },
    ) as generation:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            response = await client.post(
                _chat_completions_url(settings.llm_base_url),
                headers={"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
        data = response.json()
        message = data["choices"][0]["message"]
        content = str(message["content"]).strip()
        update_observation(
            generation,
            output={
                "id": data.get("id"),
                "model": data.get("model"),
                "finish_reason": data.get("choices", [{}])[0].get("finish_reason"),
                "content": content,
            },
            usage_details=_usage_details(data.get("usage")),
        )
    return LLMGenerationResult(
        markdown=content,
        enabled=True,
        provider=settings.llm_provider,
        model=settings.llm_model,
        request_payload=payload,
        response_payload={
            "id": data.get("id"),
            "model": data.get("model"),
            "usage": data.get("usage"),
            "finish_reason": data.get("choices", [{}])[0].get("finish_reason"),
            "content": content,
        },
    )


def _chat_completion_payload(
    settings: Settings,
    *,
    document_kind: str,
    system_instruction: str,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
) -> dict[str, Any]:
    return {
        "model": settings.llm_model,
        "temperature": settings.llm_temperature,
        "messages": [
            {
                "role": "system",
                "content": system_instruction,
            },
            {
                "role": "user",
                "content": _draft_prompt(
                    document_kind=document_kind,
                    user_input=user_input,
                    facts=facts,
                    evidence_pack=evidence_pack,
                    amount_calculation=amount_calculation,
                    template_markdown=template_markdown,
                ),
            },
        ],
    }


def _ensure_openai_compatible_config(settings: Settings) -> None:
    if settings.llm_provider != "openai_compatible":
        raise LLMConfigurationError(f"unsupported llm provider: {settings.llm_provider}")
    missing = [
        name
        for name, value in {
            "LEGAL_AGENT_LLM_BASE_URL": settings.llm_base_url,
            "LEGAL_AGENT_LLM_API_KEY": settings.llm_api_key,
            "LEGAL_AGENT_LLM_MODEL": settings.llm_model,
        }.items()
        if not value
    ]
    if missing:
        raise LLMConfigurationError(f"missing llm config: {', '.join(missing)}")


def _chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _langfuse_generation_input(*, document_kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_kind": document_kind,
        "messages": payload.get("messages") or [],
        "temperature": payload.get("temperature"),
    }


def _usage_details(raw_usage: Any) -> dict[str, int] | None:
    if not isinstance(raw_usage, dict):
        return None
    details: dict[str, int] = {}
    mapping = {
        "prompt_tokens": "input",
        "completion_tokens": "output",
        "total_tokens": "total",
    }
    for source_key, target_key in mapping.items():
        value = raw_usage.get(source_key)
        if isinstance(value, int):
            details[target_key] = value
    return details or None


def _draft_prompt(
    *,
    document_kind: str,
    user_input: str,
    facts: dict[str, Any],
    evidence_pack: list[dict[str, Any]],
    amount_calculation: dict[str, Any],
    template_markdown: str,
) -> str:
    return (
        "目标文档类型：\n"
        f"{document_kind}\n\n"
        "用户输入：\n"
        f"{user_input}\n\n"
        "已确认事实 JSON：\n"
        f"{_json(facts)}\n\n"
        "金额计算 JSON：\n"
        f"{_json(amount_calculation)}\n\n"
        "证据包 JSON：\n"
        f"{_json(evidence_pack)}\n\n"
        "基础模板草稿：\n"
        f"{template_markdown}\n"
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _parse_json_object(content: str) -> dict[str, Any]:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(cleaned[start : end + 1])
            return parsed if isinstance(parsed, dict) else {}
    return {}
