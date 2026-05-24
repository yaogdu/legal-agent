from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from legal_agent.core.claims import (
    CLAIM_DEFINITIONS,
    BASE_REQUIRED_FIELDS,
    claim_labels,
    expected_claim_types,
    facts_from_claims,
    infer_claims_from_text,
    merge_claims,
    normalize_claims,
    required_fields_for_claims,
)


@dataclass(frozen=True)
class FactRequirement:
    field: str
    label: str
    question: str
    group: str


LABOR_DISPUTE_REQUIREMENTS: tuple[FactRequirement, ...] = (
    FactRequirement("applicant_name", "申请人姓名", "请补充申请人姓名。", "party"),
    FactRequirement("company_name", "被申请人公司名称", "请补充被申请人公司名称。", "party"),
    FactRequirement("work_start_date", "入职时间", "请补充入职时间，建议使用 YYYY-MM-DD。", "employment"),
    FactRequirement("work_end_date", "离职/劳动关系结束时间", "请补充离职或劳动关系结束时间，建议使用 YYYY-MM-DD。", "employment"),
    FactRequirement("monthly_salary", "月工资金额", "请补充月工资金额。", "compensation"),
    FactRequirement("unpaid_months", "拖欠工资月份", "请补充拖欠工资的月份数量。", "compensation"),
    FactRequirement("contract_signed", "是否签署劳动合同", "请确认是否签署过书面劳动合同。", "employment"),
    FactRequirement("social_insurance_paid", "是否缴纳社保", "请确认公司是否依法缴纳社保。", "employment"),
    FactRequirement("termination_reason", "解除劳动关系原因", "请补充解除或终止劳动关系的原因。", "employment"),
    FactRequirement("evidence_available", "已有证据材料", "请列出已有证据，例如工资流水、聊天记录、考勤、劳动合同、offer、工牌等。", "evidence"),
    FactRequirement(
        "expected_claims",
        "期望主张的仲裁请求",
        "请确认本次要主张哪些诉求，可填写：2N违法解除赔偿、N+1、年终奖、加班费、未休年假补偿、拖欠工资、未签劳动合同二倍工资等。",
        "claims",
    ),
    FactRequirement("company_offer", "公司补偿方案", "请补充公司目前提出或同意的补偿方案，例如 N+1、一个月工资、未提出方案。", "claims"),
    FactRequirement("requested_termination_compensation", "解除赔偿诉求", "请确认你主张的解除赔偿口径，例如 2N、N+1 或具体金额。", "claims"),
    FactRequirement("termination_notice", "解除通知情况", "请说明是否收到解除/辞退通知、通知形式、载明理由及日期。", "evidence"),
    FactRequirement("year_end_bonus_amount", "年终奖金额", "请补充主张的年终奖金额或计算方式。", "compensation"),
    FactRequirement("year_end_bonus_basis", "年终奖依据", "请补充年终奖依据，例如劳动合同、offer、员工手册、公司制度、历史发放记录或聊天承诺。", "evidence"),
    FactRequirement("year_end_bonus_paid", "年终奖发放情况", "请说明年终奖是否已发、发了多少、未发原因。", "compensation"),
    FactRequirement("overtime_hours", "工作日延时加班小时数", "请补充工作日延时加班的大致小时数或期间。", "compensation"),
    FactRequirement("rest_day_overtime_hours", "休息日加班小时数", "请补充休息日加班小时数，以及是否安排调休。", "compensation"),
    FactRequirement("statutory_holiday_overtime_hours", "法定节假日加班小时数", "请补充法定节假日加班小时数。", "compensation"),
    FactRequirement("overtime_period", "加班期间", "请补充加班发生的时间范围，例如 2024-01 至 2024-12。", "employment"),
    FactRequirement("overtime_approval", "加班审批/安排证据", "请说明是否有加班审批、排班、考勤、聊天通知或工作成果记录。", "evidence"),
    FactRequirement("annual_leave_entitlement_days", "应休年假天数", "请补充应休年假天数。", "compensation"),
    FactRequirement("annual_leave_taken_days", "已休年假天数", "请补充已休年假天数。", "compensation"),
    FactRequirement("daily_wage", "日工资标准", "请补充日工资标准；如不清楚，可补充月工资和计薪方式。", "compensation"),
)


def required_fact_fields(task_type: str) -> list[str]:
    if task_type == "case_search":
        return ["expected_claims"]
    return list(BASE_REQUIRED_FIELDS)


def missing_fact_fields(facts: dict[str, Any], task_type: str) -> list[str]:
    claim_types = expected_claim_types(facts, task_type)
    required_fields = required_fields_for_claims(claim_types, task_type)
    return [field for field in required_fields if _is_missing(facts.get(field))]


def questions_for_missing_fields(missing_fields: list[str]) -> list[str]:
    requirements = {item.field: item for item in LABOR_DISPUTE_REQUIREMENTS}
    return [_question_for_field(field, requirements) for field in missing_fields]


def question_groups_for_missing_fields(missing_fields: list[str]) -> list[dict[str, Any]]:
    requirements = {item.field: item for item in LABOR_DISPUTE_REQUIREMENTS}
    groups: dict[str, dict[str, Any]] = {}
    for field in missing_fields:
        requirement = requirements.get(field)
        if requirement is None:
            requirement = _custom_requirement(field)
        group = groups.setdefault(
            requirement.group,
            {
                "group": requirement.group,
                "fields": [],
                "questions": [],
            },
        )
        group["fields"].append({"field": requirement.field, "label": requirement.label})
        group["questions"].append(requirement.question)
    return list(groups.values())


def infer_facts_from_input(text: str, file_ids: list[str] | None = None) -> dict[str, Any]:
    inferred: dict[str, Any] = {}
    normalized = text.replace(" ", "")
    if "没有签" in normalized or "没签" in normalized or "未签" in normalized:
        inferred["contract_signed"] = False
    if "拖欠" in normalized and "2" in normalized:
        inferred["unpaid_months"] = 2
    if "社保" in normalized:
        if any(marker in normalized for marker in ("未缴", "没缴", "没有缴", "未依法缴")):
            inferred["social_insurance_paid"] = False
        elif any(marker in normalized for marker in ("已缴", "缴纳了", "正常缴")):
            inferred["social_insurance_paid"] = True
    evidence = _infer_evidence_available(text, file_ids or [])
    if evidence:
        inferred["evidence_available"] = evidence
    claims = infer_claims_from_text(text)
    if claims:
        inferred["claims"] = claims
        inferred["expected_claims"] = claims
        inferred.update(facts_from_claims(claims))
    return inferred


def merge_inferred_claims(facts: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_claims(claims)
    if not normalized:
        return {}
    merged = merge_claims(normalize_claims(facts.get("claims") or facts.get("expected_claims")), normalized)
    return {"claims": merged, "expected_claims": merged, **facts_from_claims(merged)}


def fact_labels() -> dict[str, str]:
    labels = {item.field: item.label for item in LABOR_DISPUTE_REQUIREMENTS}
    labels.update({claim_type: label for claim_type, label in claim_labels().items()})
    return labels


def _infer_evidence_available(text: str, file_ids: list[str]) -> list[str]:
    evidence = []
    markers = {
        "salary_flow": ("工资流水", "银行流水", "工资条"),
        "chat_record": ("聊天记录", "微信", "企业微信", "钉钉"),
        "attendance_record": ("考勤", "打卡"),
        "labor_contract": ("劳动合同", "合同"),
        "offer": ("offer", "录用通知"),
        "badge": ("工牌", "门禁"),
        "termination_notice": ("解除通知", "辞退通知", "离职证明"),
        "social_insurance_record": ("社保记录", "社保缴纳"),
    }
    for key, words in markers.items():
        if any(word in text for word in words):
            evidence.append(key)
    if file_ids:
        evidence.append("uploaded_material")
    return sorted(set(evidence))


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _question_for_field(field: str, requirements: dict[str, FactRequirement]) -> str:
    requirement = requirements.get(field)
    if requirement is not None:
        return requirement.question
    if field.startswith("claim_") and field.endswith("_basis"):
        return "请补充该自定义诉求的事实依据或制度/合同依据。"
    if field.startswith("claim_") and field.endswith("_amount"):
        return "请补充该自定义诉求的金额或计算方式；如暂不确定，可说明待核算。"
    if field.startswith("claim_") and field.endswith("_evidence"):
        return "请补充该自定义诉求已有证据，例如合同、制度、聊天记录、流水或其他材料。"
    return f"请补充 {field}。"


def _custom_requirement(field: str) -> FactRequirement:
    if field.startswith("claim_") and field.endswith("_basis"):
        return FactRequirement(field, "自定义诉求依据", _question_for_field(field, {}), "claims")
    if field.startswith("claim_") and field.endswith("_amount"):
        return FactRequirement(field, "自定义诉求金额", _question_for_field(field, {}), "compensation")
    if field.startswith("claim_") and field.endswith("_evidence"):
        return FactRequirement(field, "自定义诉求证据", _question_for_field(field, {}), "evidence")
    return FactRequirement(field, field, _question_for_field(field, {}), "custom")
