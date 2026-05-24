from __future__ import annotations

import re
from typing import Any


CLAIM_DEFINITIONS: dict[str, dict[str, Any]] = {
    "illegal_termination_damages": {
        "label": "违法解除赔偿金",
        "aliases": ["违法解除", "无故辞退", "2n", "2N", "赔偿金"],
        "required_fields": [
            "work_start_date",
            "work_end_date",
            "monthly_salary",
            "termination_reason",
            "company_offer",
            "requested_termination_compensation",
            "termination_notice",
        ],
        "evidence": ["termination_notice", "salary_flow", "chat_record", "labor_contract"],
    },
    "economic_compensation": {
        "label": "经济补偿金",
        "aliases": ["经济补偿", "n+1", "N+1", "补偿金"],
        "required_fields": ["work_start_date", "work_end_date", "monthly_salary", "termination_reason", "company_offer"],
        "evidence": ["termination_notice", "salary_flow", "labor_contract"],
    },
    "unpaid_salary": {
        "label": "拖欠工资",
        "aliases": ["拖欠工资", "工资", "劳动报酬", "欠薪"],
        "required_fields": ["monthly_salary", "unpaid_months"],
        "evidence": ["salary_flow", "chat_record", "attendance_record"],
    },
    "double_salary": {
        "label": "未签书面劳动合同二倍工资差额",
        "aliases": ["未签劳动合同", "没签劳动合同", "二倍工资", "双倍工资"],
        "required_fields": ["work_start_date", "work_end_date", "monthly_salary", "contract_signed"],
        "evidence": ["salary_flow", "chat_record", "attendance_record", "labor_contract"],
    },
    "year_end_bonus": {
        "label": "年终奖",
        "aliases": ["年终奖", "奖金", "绩效奖金", "十三薪", "13薪"],
        "required_fields": ["year_end_bonus_amount", "year_end_bonus_basis", "year_end_bonus_paid"],
        "evidence": ["bonus_policy", "salary_flow", "chat_record", "offer", "labor_contract"],
    },
    "overtime_pay": {
        "label": "加班费",
        "aliases": ["加班费", "加班工资", "延时加班", "周末加班", "法定节假日加班"],
        "required_fields": ["overtime_hours", "overtime_period", "overtime_approval", "rest_day_overtime_hours", "statutory_holiday_overtime_hours"],
        "evidence": ["attendance_record", "overtime_approval", "chat_record", "salary_flow"],
    },
    "unused_annual_leave_pay": {
        "label": "未休年休假工资报酬",
        "aliases": ["未休年假", "年假补偿", "年休假", "未休年休假"],
        "required_fields": ["annual_leave_entitlement_days", "annual_leave_taken_days", "daily_wage"],
        "evidence": ["attendance_record", "leave_record", "salary_flow", "chat_record"],
    },
    "social_insurance": {
        "label": "社保相关请求",
        "aliases": ["社保", "未缴社保", "补缴社保"],
        "required_fields": ["social_insurance_paid"],
        "evidence": ["social_insurance_record", "salary_flow"],
    },
}


BASE_REQUIRED_FIELDS = [
    "applicant_name",
    "company_name",
    "evidence_available",
    "expected_claims",
]

DEFAULT_DOCUMENT_CLAIMS = ["unpaid_salary"]
DEFAULT_CASE_SEARCH_CLAIMS = ["unpaid_salary", "illegal_termination_damages"]


def claim_labels() -> dict[str, str]:
    return {claim_type: str(definition["label"]) for claim_type, definition in CLAIM_DEFINITIONS.items()}


def infer_claims_from_text(text: str) -> list[dict[str, Any]]:
    normalized = text.replace(" ", "")
    claims: list[dict[str, Any]] = []
    for claim_type, definition in CLAIM_DEFINITIONS.items():
        aliases = [str(alias) for alias in definition.get("aliases") or []]
        if any(alias in normalized for alias in aliases):
            claims.append(
                _claim(
                    claim_type,
                    source="rule",
                    confidence=0.72,
                    requested=_requested_for_claim(claim_type, normalized),
                    company_offer=_company_offer(normalized),
                )
            )
    return normalize_claims(claims)


def normalize_claims(raw_claims: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_claims or []:
        if isinstance(raw, str):
            claim_type = _claim_type_from_text(raw)
            raw = {"type": claim_type, "requested": _requested_for_claim(claim_type, raw.replace(" ", "")), "source": "user"}
        if not isinstance(raw, dict):
            continue
        raw_type = str(raw.get("type") or raw.get("claim_type") or "")
        claim_type = _normalize_claim_type(raw_type)
        if claim_type not in CLAIM_DEFINITIONS and claim_type != "custom":
            raw = {**raw, "key": _custom_claim_key(raw_type or str(raw.get("label") or raw.get("name") or raw.get("requested") or "custom_claim"))}
            claim_type = "custom"
        dedupe_key = _claim_dedupe_key(claim_type, raw)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        normalized.append(_normalized_claim(claim_type, raw))
    return normalized


def expected_claim_types(facts: dict[str, Any], task_type: str) -> list[str]:
    claims = normalize_claims(facts.get("claims"))
    if claims:
        return [_claim_dedupe_key(str(claim["type"]), claim) for claim in claims]
    expected = facts.get("expected_claims")
    if expected:
        return [_claim_dedupe_key(str(claim["type"]), claim) for claim in normalize_claims(expected)]
    if task_type == "case_search":
        return list(DEFAULT_CASE_SEARCH_CLAIMS)
    return list(DEFAULT_DOCUMENT_CLAIMS)


def required_fields_for_claims(claim_types: list[str], task_type: str) -> list[str]:
    required = [] if task_type == "case_search" else list(BASE_REQUIRED_FIELDS)
    for claim_type in claim_types:
        definition = CLAIM_DEFINITIONS.get(claim_type)
        custom_key = _custom_key_from_dedupe_key(claim_type)
        if not definition and custom_key:
            for field in _custom_required_fields(custom_key):
                if field not in required:
                    required.append(field)
            continue
        if not definition:
            continue
        for field in definition.get("required_fields") or []:
            if field not in required:
                required.append(str(field))
    return required


def claim_summary(claims: list[dict[str, Any]]) -> list[str]:
    lines = []
    for claim in claims:
        claim_type = str(claim.get("type") or "")
        label = CLAIM_DEFINITIONS.get(claim_type, {}).get("label") or claim.get("label") or claim_type
        detail = []
        if claim.get("requested"):
            detail.append(f"诉求：{claim['requested']}")
        if claim.get("company_offer"):
            detail.append(f"公司方案：{claim['company_offer']}")
        lines.append(f"{label}{'（' + '，'.join(detail) + '）' if detail else ''}")
    return lines


def merge_claims(primary: list[dict[str, Any]], secondary: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {_claim_dedupe_key(str(claim.get("type")), claim): dict(claim) for claim in normalize_claims(primary)}
    for claim in normalize_claims(secondary):
        claim_key = _claim_dedupe_key(str(claim.get("type")), claim)
        if claim_key not in merged:
            merged[claim_key] = dict(claim)
            continue
        merged[claim_key] = {**claim, **{key: value for key, value in merged[claim_key].items() if value not in (None, "", [], {})}}
    return list(merged.values())


def facts_from_claims(claims: list[dict[str, Any]]) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for claim in normalize_claims(claims):
        if claim.get("company_offer") and not facts.get("company_offer"):
            facts["company_offer"] = claim["company_offer"]
        requested = claim.get("requested")
        claim_type = claim.get("type")
        if requested and claim_type == "illegal_termination_damages":
            facts["requested_termination_compensation"] = requested
        elif requested and claim_type == "economic_compensation" and not facts.get("requested_termination_compensation"):
            facts["requested_termination_compensation"] = requested
    return facts


def _claim(
    claim_type: str,
    *,
    source: str,
    confidence: float,
    requested: Any = None,
    company_offer: Any = None,
    notes: Any = None,
) -> dict[str, Any]:
    claim = {
        "type": claim_type,
        "label": CLAIM_DEFINITIONS[claim_type]["label"],
        "source": source,
        "confidence": confidence,
    }
    if requested not in (None, "", [], {}):
        claim["requested"] = str(requested)
    if company_offer not in (None, "", [], {}):
        claim["company_offer"] = str(company_offer)
    if notes not in (None, "", [], {}):
        claim["notes"] = notes
    return claim


def _normalized_claim(claim_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    if claim_type in CLAIM_DEFINITIONS:
        return _claim(
            claim_type,
            source=str(raw.get("source") or "user"),
            confidence=_confidence(raw.get("confidence")),
            requested=raw.get("requested"),
            company_offer=raw.get("company_offer"),
            notes=raw.get("notes"),
        )
    label = str(raw.get("label") or raw.get("name") or raw.get("requested") or raw.get("type") or "自定义诉求").strip()
    claim = {
        "type": "custom",
        "key": _custom_claim_key(str(raw.get("key") or raw.get("type") or label)),
        "label": label,
        "source": str(raw.get("source") or "user"),
        "confidence": _confidence(raw.get("confidence")),
        "custom": True,
    }
    for key in ("requested", "company_offer", "basis", "facts", "evidence", "notes"):
        value = raw.get(key)
        if value not in (None, "", [], {}):
            claim[key] = value
    return claim


def _normalize_claim_type(value: str) -> str:
    lowered = value.strip().lower()
    aliases = {
        "termination_compensation_or_damages": "illegal_termination_damages",
        "termination_damages": "illegal_termination_damages",
        "illegal_termination": "illegal_termination_damages",
        "n_plus_one": "economic_compensation",
        "unpaid_wage": "unpaid_salary",
        "bonus": "year_end_bonus",
        "annual_bonus": "year_end_bonus",
        "annual_leave": "unused_annual_leave_pay",
        "unused_annual_leave": "unused_annual_leave_pay",
    }
    if lowered in CLAIM_DEFINITIONS:
        return lowered
    return aliases.get(lowered, lowered)


def _claim_type_from_text(value: str) -> str:
    normalized = value.replace(" ", "")
    lowered = normalized.lower()
    direct = _normalize_claim_type(lowered)
    if direct in CLAIM_DEFINITIONS:
        return direct
    for claim_type, definition in CLAIM_DEFINITIONS.items():
        label = str(definition.get("label") or "")
        aliases = [str(alias) for alias in definition.get("aliases") or []]
        if label and label in normalized:
            return claim_type
        if any(alias.lower() in lowered for alias in aliases):
            return claim_type
    return "custom"


def _custom_claim_key(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", value.strip()).strip("_").lower()
    return slug or "claim"


def _custom_required_fields(claim_key: str) -> list[str]:
    suffix = _custom_claim_key(claim_key).replace("-", "_")
    return [
        f"claim_{suffix}_basis",
        f"claim_{suffix}_amount",
        f"claim_{suffix}_evidence",
    ]


def _claim_dedupe_key(claim_type: str, claim: dict[str, Any]) -> str:
    if claim_type == "custom":
        return ".".join(["custom", _custom_claim_key(str(claim.get("key") or claim.get("label") or claim.get("requested") or "claim"))])
    return claim_type


def _custom_key_from_dedupe_key(value: str) -> str | None:
    parts = value.split(".", 1)
    if len(parts) != 2 or parts[0] != "custom":
        return None
    return _custom_claim_key(parts[1])


def _confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.7
    return max(0.0, min(1.0, numeric))


def _requested_for_claim(claim_type: str, normalized_text: str) -> str | None:
    if claim_type == "illegal_termination_damages" and "2n" in normalized_text.lower():
        return "2N"
    if claim_type == "economic_compensation" and "n+1" in normalized_text.lower():
        return "N+1"
    return None


def _company_offer(normalized_text: str) -> str | None:
    if re.search(r"(公司|单位).{0,8}(同意|只给|愿意给|给).{0,8}n\+1", normalized_text, flags=re.IGNORECASE):
        return "N+1"
    return None
