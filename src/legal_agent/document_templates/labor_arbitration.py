from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from legal_agent.core.claims import CLAIM_DEFINITIONS, claim_summary, normalize_claims


TEMPLATE_ID = "labor_arbitration_application.cn-bj.v1"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "labor_arbitration_application.cn-bj.md"


@dataclass(frozen=True)
class RenderedDocument:
    template_id: str
    document_type: str
    title: str
    format: str
    markdown: str
    fields: dict[str, Any]
    pending_fields: list[str]


def render_labor_arbitration_application(
    facts: dict[str, Any],
    *,
    evidence_pack: list[dict[str, Any]] | None = None,
    amount_calculation: dict[str, Any] | None = None,
) -> RenderedDocument:
    evidence_pack = evidence_pack or []
    fields = _fields(facts, evidence_pack, amount_calculation or {})
    markdown = TEMPLATE_PATH.read_text(encoding="utf-8").format(**fields)
    pending_fields = [key for key, value in fields.items() if value == "待补充"]
    return RenderedDocument(
        template_id=TEMPLATE_ID,
        document_type="labor_arbitration_application",
        title="劳动人事争议仲裁申请书",
        format="markdown",
        markdown=markdown,
        fields=fields,
        pending_fields=pending_fields,
    )


def _fields(facts: dict[str, Any], evidence_pack: list[dict[str, Any]], amount_calculation: dict[str, Any]) -> dict[str, Any]:
    unpaid_months = _int_or_none(facts.get("unpaid_months"))
    monthly_salary = _float_or_none(facts.get("monthly_salary"))
    claims_data = normalize_claims(facts.get("claims") or facts.get("expected_claims"))
    claim_types = [str(claim["type"]) for claim in claims_data] or ["unpaid_salary"]
    amount_by_item = _amounts_by_item(amount_calculation)
    unpaid_salary_amount = amount_by_item.get("unpaid_salary")
    if unpaid_salary_amount is None and monthly_salary is not None and unpaid_months is not None:
        unpaid_salary_amount = monthly_salary * unpaid_months
    contract_signed = facts.get("contract_signed")

    claims = _claim_lines(claim_types, claims_data, facts, amount_by_item, unpaid_salary_amount, contract_signed)
    facts_and_reasons = _fact_lines(claim_types, claims_data, facts, unpaid_months, monthly_salary, contract_signed)

    evidence_lines = [
        "劳动合同、入职登记、考勤记录、工资流水、社保记录、解除/辞退通知、聊天记录等。"
    ]
    if claims_data:
        evidence_lines.append("本案诉求类型：" + "；".join(claim_summary(claims_data)) + "。")
    for claim_type in claim_types:
        evidence_names = _evidence_names(claim_type)
        if evidence_names:
            evidence_lines.append(f"{_claim_label(claim_type)}建议证据：{evidence_names}。")
    for evidence in evidence_pack:
        source_name = evidence.get("source_name") or evidence.get("title")
        quote = evidence.get("quote")
        if source_name and quote:
            anchor = evidence.get("citation_anchor") or "未标注条款"
            url = evidence.get("source_url") or "无来源 URL"
            evidence_lines.append(f"{source_name}（{anchor}）：{quote} 来源：{url}")

    legal_basis = _legal_basis(evidence_pack)

    return {
        "applicant_name": _value(facts, "applicant_name"),
        "applicant_id": _value(facts, "applicant_id"),
        "applicant_phone": _value(facts, "applicant_phone"),
        "applicant_address": _value(facts, "applicant_address"),
        "company_name": _value(facts, "company_name"),
        "company_credit_code": _value(facts, "company_credit_code"),
        "company_representative": _value(facts, "company_representative"),
        "company_address": _value(facts, "company_address"),
        "claims_md": _numbered(claims),
        "facts_and_reasons_md": _paragraphs(facts_and_reasons),
        "evidence_list_md": _numbered(evidence_lines),
        "legal_basis_md": _numbered(legal_basis),
        "arbitration_committee": facts.get("arbitration_committee") or "有管辖权的劳动人事争议仲裁委员会",
        "application_date": facts.get("application_date") or date.today().isoformat(),
    }


def _claim_lines(
    claim_types: list[str],
    claims_data: list[dict[str, Any]],
    facts: dict[str, Any],
    amount_by_item: dict[str, float | None],
    unpaid_salary_amount: float | None,
    contract_signed: Any,
) -> list[str]:
    claims: list[str] = []
    if "unpaid_salary" in claim_types:
        if unpaid_salary_amount is not None:
            claims.append(f"请求裁决被申请人支付拖欠工资人民币 {_money(unpaid_salary_amount)} 元。")
        else:
            claims.append("请求裁决被申请人支付拖欠工资，具体金额以工资标准和拖欠期间核算。")
    if "double_salary" in claim_types or contract_signed is False:
        amount = amount_by_item.get("double_salary")
        if amount is not None:
            claims.append(f"请求裁决被申请人支付未签书面劳动合同二倍工资差额人民币 {_money(amount)} 元。")
        else:
            claims.append("请求裁决被申请人支付未签书面劳动合同二倍工资差额，具体期间和金额待核算。")
    if "illegal_termination_damages" in claim_types:
        amount = amount_by_item.get("illegal_termination_damages")
        requested = facts.get("requested_termination_compensation") or "2N"
        offer = f"；被申请人现方案为 {facts['company_offer']}" if facts.get("company_offer") else ""
        if amount is not None:
            claims.append(f"请求裁决被申请人支付违法解除劳动合同赔偿金人民币 {_money(amount)} 元（主张口径：{requested}{offer}）。")
        else:
            claims.append(f"请求裁决被申请人支付违法解除劳动合同赔偿金（主张口径：{requested}{offer}），具体金额待核算。")
    if "economic_compensation" in claim_types and "illegal_termination_damages" not in claim_types:
        amount = amount_by_item.get("economic_compensation")
        if amount is not None:
            claims.append(f"请求裁决被申请人支付解除劳动合同经济补偿人民币 {_money(amount)} 元。")
        else:
            claims.append("请求裁决被申请人支付解除劳动合同经济补偿，具体金额待核算。")
    if "year_end_bonus" in claim_types:
        amount = amount_by_item.get("year_end_bonus")
        if amount is not None:
            claims.append(f"请求裁决被申请人支付年终奖人民币 {_money(amount)} 元。")
        else:
            claims.append("请求裁决被申请人支付应发未发年终奖，具体金额或计算方式待核算。")
    if "overtime_pay" in claim_types:
        amount = amount_by_item.get("overtime_pay")
        if amount is not None:
            claims.append(f"请求裁决被申请人支付加班费人民币 {_money(amount)} 元。")
        else:
            claims.append("请求裁决被申请人支付加班费，具体小时、倍率和金额待核算。")
    if "unused_annual_leave_pay" in claim_types:
        amount = amount_by_item.get("unused_annual_leave_pay")
        if amount is not None:
            claims.append(f"请求裁决被申请人支付未休年休假工资报酬人民币 {_money(amount)} 元。")
        else:
            claims.append("请求裁决被申请人支付未休年休假工资报酬，具体天数和金额待核算。")
    if "social_insurance" in claim_types:
        claims.append("请求依法处理被申请人未依法缴纳社会保险相关事项。")
    for claim in claims_data:
        claim_type = str(claim.get("type") or "")
        if claim_type != "custom":
            continue
        amount = amount_by_item.get(f"custom.{claim.get('key')}")
        label = str(claim.get("label") or "自定义诉求")
        requested = str(claim.get("requested") or label)
        if amount is not None:
            claims.append(f"请求裁决被申请人支付{requested}人民币 {_money(amount)} 元。")
        else:
            claims.append(f"请求裁决被申请人承担或支付{requested}，具体金额和依据待核算。")
    return claims or ["请求裁决被申请人承担劳动争议相关给付责任，具体请求待补充。"]


def _fact_lines(
    claim_types: list[str],
    claims_data: list[dict[str, Any]],
    facts: dict[str, Any],
    unpaid_months: int | None,
    monthly_salary: float | None,
    contract_signed: Any,
) -> list[str]:
    facts_and_reasons = [
        (
            f"申请人于 {_value(facts, 'work_start_date')} 入职被申请人，"
            f"劳动关系至 {_value(facts, 'work_end_date')}。"
        ),
        f"申请人月工资标准为人民币 {_money(monthly_salary) if monthly_salary is not None else '待补充'} 元。",
    ]
    if "unpaid_salary" in claim_types:
        facts_and_reasons.append(f"被申请人拖欠工资期间为 {unpaid_months if unpaid_months is not None else '待补充'} 个月。")
    if contract_signed is False or "double_salary" in claim_types:
        facts_and_reasons.append("双方未签订书面劳动合同，相关责任需结合入职时间、工资支付记录和法律规定核算。")
    if "illegal_termination_damages" in claim_types or "economic_compensation" in claim_types:
        facts_and_reasons.append(f"被申请人解除或终止劳动关系的原因/情形为：{_value(facts, 'termination_reason')}。")
        if facts.get("company_offer"):
            facts_and_reasons.append(f"被申请人目前提出或同意的补偿方案为：{facts['company_offer']}。")
        if facts.get("requested_termination_compensation"):
            facts_and_reasons.append(f"申请人主张的解除补偿/赔偿口径为：{facts['requested_termination_compensation']}。")
        if facts.get("termination_notice"):
            facts_and_reasons.append(f"解除通知及载明理由情况：{facts['termination_notice']}。")
    if "year_end_bonus" in claim_types:
        facts_and_reasons.append(
            f"申请人主张年终奖金额或计算方式为：{_value(facts, 'year_end_bonus_amount')}；"
            f"依据为：{_value(facts, 'year_end_bonus_basis')}；"
            f"发放情况为：{_value(facts, 'year_end_bonus_paid')}。"
        )
    if "overtime_pay" in claim_types:
        facts_and_reasons.append(
            f"申请人主张加班期间为 {_value(facts, 'overtime_period')}，"
            f"工作日延时加班 {_value(facts, 'overtime_hours')} 小时，"
            f"休息日加班 {_value(facts, 'rest_day_overtime_hours')} 小时，"
            f"法定节假日加班 {_value(facts, 'statutory_holiday_overtime_hours')} 小时；"
            f"加班安排或审批证据为：{_value(facts, 'overtime_approval')}。"
        )
    if "unused_annual_leave_pay" in claim_types:
        facts_and_reasons.append(
            f"申请人主张应休年休假 {_value(facts, 'annual_leave_entitlement_days')} 天，"
            f"已休 {_value(facts, 'annual_leave_taken_days')} 天，"
            f"日工资标准为人民币 {_value(facts, 'daily_wage')} 元。"
        )
    for claim in claims_data:
        claim_type = str(claim.get("type") or "")
        if claim_type != "custom":
            continue
        suffix = str(claim.get("key") or "claim").replace("-", "_")
        label = str(claim.get("label") or claim.get("requested") or "自定义诉求")
        facts_and_reasons.append(
            f"关于{label}：事实或依据为 {_value(facts, f'claim_{suffix}_basis')}；"
            f"金额或计算方式为 {_value(facts, f'claim_{suffix}_amount')}；"
            f"证据为 {_value(facts, f'claim_{suffix}_evidence')}。"
        )
    return facts_and_reasons


def _value(facts: dict[str, Any], key: str) -> str:
    value = facts.get(key)
    if value in (None, ""):
        return "待补充"
    return str(value)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def _paragraphs(items: list[str]) -> str:
    return "\n\n".join(items)


def _legal_basis(evidence_pack: list[dict[str, Any]]) -> list[str]:
    basis: list[str] = []
    seen: set[str] = set()
    for evidence in evidence_pack:
        if evidence.get("authority_level") not in {"A0", "A1", "B0"}:
            continue
        if evidence.get("source_type") not in {"law", "judicial_interpretation", "administrative_regulation", "department_rule", "regulation"}:
            continue
        source_name = evidence.get("source_name")
        anchor = evidence.get("citation_anchor")
        supported_claim = evidence.get("supported_claim")
        if not source_name or not anchor:
            continue
        line = f"{anchor}：{supported_claim or evidence.get('quote', '')}"
        if line not in seen:
            seen.add(line)
            basis.append(line)
    if basis:
        return basis
    return [
        "《中华人民共和国劳动争议调解仲裁法》第二条。",
        "《中华人民共和国劳动合同法》第八十二条。",
    ]


def _amount_item(amount_calculation: dict[str, Any], item_name: str) -> float | None:
    for item in amount_calculation.get("items") or []:
        if item.get("item") != item_name:
            continue
        return _float_or_none(item.get("amount"))
    return None


def _amounts_by_item(amount_calculation: dict[str, Any]) -> dict[str, float | None]:
    amounts: dict[str, float | None] = {}
    for item in amount_calculation.get("items") or []:
        if not isinstance(item, dict) or not item.get("item"):
            continue
        item_name = str(item.get("item"))
        if item_name == "custom" and item.get("key"):
            item_name = f"custom.{item['key']}"
        amounts[item_name] = _float_or_none(item.get("amount"))
    return amounts


def _claim_label(claim_type: str) -> str:
    return str(CLAIM_DEFINITIONS.get(claim_type, {}).get("label") or claim_type)


def _evidence_names(claim_type: str) -> str:
    labels = {
        "termination_notice": "解除/辞退通知",
        "salary_flow": "工资流水",
        "chat_record": "聊天记录",
        "labor_contract": "劳动合同",
        "bonus_policy": "年终奖制度或承诺",
        "offer": "offer",
        "attendance_record": "考勤记录",
        "overtime_approval": "加班审批",
        "leave_record": "休假记录",
        "social_insurance_record": "社保记录",
    }
    evidence = CLAIM_DEFINITIONS.get(claim_type, {}).get("evidence") or []
    return "、".join(labels.get(str(item), str(item)) for item in evidence)


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


def _money(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.2f}"
