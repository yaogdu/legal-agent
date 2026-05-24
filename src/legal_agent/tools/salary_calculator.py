from __future__ import annotations

from datetime import date
from typing import Any

from legal_agent.core.claims import normalize_claims


SALARY_CALCULATOR_INPUT_SCHEMA = {
    "type": "object",
    "required": ["monthly_salary", "contract_signed", "jurisdiction"],
    "properties": {
        "claims": {"type": "array", "items": {"type": "object"}},
        "monthly_salary": {"type": ["number", "string", "null"]},
        "daily_wage": {"type": ["number", "string", "null"]},
        "unpaid_months": {"type": ["integer", "number", "string", "null"]},
        "work_start_date": {"type": ["string", "null"]},
        "work_end_date": {"type": ["string", "null"]},
        "contract_signed": {"type": ["boolean", "null"]},
        "termination_reason": {"type": ["string", "null"]},
        "company_offer": {"type": ["string", "null"]},
        "requested_termination_compensation": {"type": ["string", "null"]},
        "year_end_bonus_amount": {"type": ["number", "string", "null"]},
        "overtime_hours": {"type": ["number", "string", "null"]},
        "rest_day_overtime_hours": {"type": ["number", "string", "null"]},
        "statutory_holiday_overtime_hours": {"type": ["number", "string", "null"]},
        "annual_leave_entitlement_days": {"type": ["number", "string", "null"]},
        "annual_leave_taken_days": {"type": ["number", "string", "null"]},
        "jurisdiction": {"type": "string"},
        "calculation_items": {"type": "array", "items": {"type": "string"}},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "legal_evidence_refs": {"type": "array", "items": {"type": "string"}},
        "_logical_operation": {"type": "string"},
    },
}

SALARY_CALCULATOR_OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["status", "items", "notes"],
    "properties": {
        "status": {"type": "string"},
        "items": {"type": "array", "items": {"type": "object"}},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def salary_calculator(args: dict[str, Any]) -> dict[str, Any]:
    monthly_salary = _float_or_none(args.get("monthly_salary"))
    daily_wage = _float_or_none(args.get("daily_wage"))
    if daily_wage is None and monthly_salary is not None:
        daily_wage = monthly_salary / 21.75
    unpaid_months = _int_or_none(args.get("unpaid_months"))
    evidence_refs = list(args.get("evidence_refs") or [])
    legal_evidence_refs = list(args.get("legal_evidence_refs") or [])
    claims = normalize_claims(args.get("claims") or [])
    claim_types = _claim_types(args, claims)
    items: list[dict[str, Any]] = []
    notes = [
        "金额仅为辅助估算，最终以仲裁机构认定为准。",
        "解除赔偿、年终奖、加班费、未休年假工资报酬需要结合时效、制度依据、证据和当地口径复核。",
    ]

    if "unpaid_salary" in claim_types:
        unpaid_salary = monthly_salary * unpaid_months if monthly_salary is not None and unpaid_months is not None else None
        items.append(
            {
                "item": "unpaid_salary",
                "amount": unpaid_salary,
                "currency": "CNY",
                "certainty": "estimated" if unpaid_salary is not None else "requires_more_facts",
                "formula": "monthly_salary * unpaid_months",
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )

    if "double_salary" in claim_types and args.get("contract_signed") is False:
        double_salary_months = _double_salary_months(args)
        double_salary_amount = monthly_salary * double_salary_months if double_salary_months is not None else None
        items.append(
            {
                "item": "double_salary",
                "amount": double_salary_amount,
                "currency": "CNY",
                "certainty": "requires_review",
                "formula": "monthly_salary * eligible_uncontracted_months",
                "eligible_months": double_salary_months,
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )

    if "illegal_termination_damages" in claim_types:
        work_years = _work_years(args)
        amount = monthly_salary * work_years * 2 if monthly_salary is not None and work_years is not None else None
        items.append(
            {
                "item": "illegal_termination_damages",
                "amount": amount,
                "currency": "CNY",
                "certainty": "requires_review",
                "formula": "monthly_salary * service_years * 2",
                "service_years": work_years,
                "requested": args.get("requested_termination_compensation") or "2N",
                "company_offer": args.get("company_offer"),
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )
    elif "economic_compensation" in claim_types:
        work_years = _work_years(args)
        amount = monthly_salary * work_years if monthly_salary is not None and work_years is not None else None
        items.append(
            {
                "item": "economic_compensation",
                "amount": amount,
                "currency": "CNY",
                "certainty": "requires_review",
                "formula": "monthly_salary * service_years",
                "service_years": work_years,
                "company_offer": args.get("company_offer"),
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )

    if "year_end_bonus" in claim_types:
        amount = _float_or_none(args.get("year_end_bonus_amount"))
        items.append(
            {
                "item": "year_end_bonus",
                "amount": amount,
                "currency": "CNY",
                "certainty": "estimated" if amount is not None else "requires_more_facts",
                "formula": "amount claimed by user or bonus policy",
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )

    if "overtime_pay" in claim_types:
        weekday_hours = _float_or_none(args.get("overtime_hours")) or 0
        rest_day_hours = _float_or_none(args.get("rest_day_overtime_hours")) or 0
        holiday_hours = _float_or_none(args.get("statutory_holiday_overtime_hours")) or 0
        hourly_wage = daily_wage / 8 if daily_wage is not None else None
        amount = None
        if hourly_wage is not None and any(value > 0 for value in (weekday_hours, rest_day_hours, holiday_hours)):
            amount = hourly_wage * weekday_hours * 1.5 + hourly_wage * rest_day_hours * 2 + hourly_wage * holiday_hours * 3
        items.append(
            {
                "item": "overtime_pay",
                "amount": amount,
                "currency": "CNY",
                "certainty": "estimated" if amount is not None else "requires_more_facts",
                "formula": "hourly_wage * weekday_hours * 150% + rest_day_hours * 200% + statutory_holiday_hours * 300%",
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )

    if "unused_annual_leave_pay" in claim_types:
        entitled = _float_or_none(args.get("annual_leave_entitlement_days"))
        taken = _float_or_none(args.get("annual_leave_taken_days")) or 0
        unused_days = max(0, entitled - taken) if entitled is not None else None
        amount = daily_wage * unused_days * 2 if daily_wage is not None and unused_days is not None else None
        items.append(
            {
                "item": "unused_annual_leave_pay",
                "amount": amount,
                "currency": "CNY",
                "certainty": "estimated" if amount is not None else "requires_more_facts",
                "formula": "daily_wage * unused_days * 200%",
                "unused_days": unused_days,
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )

    for claim in claims:
        if claim.get("type") != "custom":
            continue
        suffix = str(claim.get("key") or "claim").replace("-", "_")
        amount = _float_or_none(args.get(f"claim_{suffix}_amount"))
        items.append(
            {
                "item": "custom",
                "key": suffix,
                "label": claim.get("label"),
                "amount": amount,
                "currency": "CNY",
                "certainty": "requires_review" if amount is not None else "requires_more_facts",
                "formula": "custom claim amount supplied by user or requires manual calculation",
                "basis_evidence_ids": evidence_refs,
                "legal_evidence_ids": legal_evidence_refs,
            }
        )

    status = "calculated" if items and any(item.get("amount") is not None for item in items) else "requires_more_facts"
    return {"status": status, "items": items, "notes": notes}


def _double_salary_months(args: dict[str, Any]) -> int | None:
    start = _date_or_none(args.get("work_start_date"))
    end = _date_or_none(args.get("work_end_date"))
    if start is None or end is None or end <= start:
        return None
    worked_months = (end.year - start.year) * 12 + end.month - start.month
    if end.day >= start.day:
        worked_months += 1
    return max(0, min(11, worked_months - 1))


def _work_years(args: dict[str, Any]) -> float | None:
    start = _date_or_none(args.get("work_start_date"))
    end = _date_or_none(args.get("work_end_date"))
    if start is None or end is None or end <= start:
        return None
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day >= start.day:
        months += 1
    years = months / 12
    return max(0.5, round(years * 2) / 2)


def _claim_types(args: dict[str, Any], claims: list[dict[str, Any]]) -> set[str]:
    if claims:
        return {str(item.get("type")) for item in claims if item.get("type") and item.get("type") != "custom"}
    items = args.get("calculation_items") or []
    return {str(item) for item in items if item}


def _date_or_none(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
