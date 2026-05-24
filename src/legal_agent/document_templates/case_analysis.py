from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from legal_agent.core.claims import CLAIM_DEFINITIONS, normalize_claims


TEMPLATE_ID = "labor_dispute_case_analysis.cn.v1"
TEMPLATE_PATH = Path(__file__).resolve().parent / "templates" / "labor_dispute_case_analysis.cn.md"
LEGAL_SOURCE_TYPES = {"law", "judicial_interpretation", "administrative_regulation", "department_rule", "regulation"}
CASE_SOURCE_TYPES = {"case", "typical_case", "guiding_case"}


@dataclass(frozen=True)
class RenderedCaseAnalysis:
    template_id: str
    document_type: str
    title: str
    format: str
    markdown: str
    fields: dict[str, Any]
    pending_fields: list[str]


def render_labor_dispute_case_analysis(
    facts: dict[str, Any],
    *,
    evidence_pack: list[dict[str, Any]] | None = None,
    amount_calculation: dict[str, Any] | None = None,
) -> RenderedCaseAnalysis:
    evidence_pack = evidence_pack or []
    fields = _fields(facts, evidence_pack, amount_calculation or {})
    markdown = TEMPLATE_PATH.read_text(encoding="utf-8").format(**fields)
    pending_fields = [key for key, value in fields.items() if value == "待补充"]
    return RenderedCaseAnalysis(
        template_id=TEMPLATE_ID,
        document_type="labor_dispute_case_analysis",
        title="劳动争议案情分析报告",
        format="markdown",
        markdown=markdown,
        fields=fields,
        pending_fields=pending_fields,
    )


def _fields(facts: dict[str, Any], evidence_pack: list[dict[str, Any]], amount_calculation: dict[str, Any]) -> dict[str, Any]:
    claim_types = [str(claim["type"]) for claim in normalize_claims(facts.get("claims") or facts.get("expected_claims"))] or ["unpaid_salary"]
    case_summary = [
        (
            f"申请人 {_value(facts, 'applicant_name')} 与被申请人 {_value(facts, 'company_name')} "
            f"存在劳动关系，入职时间为 {_value(facts, 'work_start_date')}，劳动关系结束时间为 {_value(facts, 'work_end_date')}。"
        ),
        f"已知月工资标准为 {_value(facts, 'monthly_salary')} 元。",
    ]
    if "unpaid_salary" in claim_types:
        case_summary.append(f"拖欠工资期间为 {_value(facts, 'unpaid_months')} 个月。")
    if facts.get("contract_signed") is False:
        case_summary.append("已确认或主张双方未签订书面劳动合同。")
    if facts.get("termination_reason"):
        case_summary.append(f"解除或终止劳动关系情形：{facts['termination_reason']}。")
    if facts.get("company_offer"):
        case_summary.append(f"公司补偿方案：{facts['company_offer']}。")

    issues = []
    if "unpaid_salary" in claim_types:
        issues.append("拖欠工资或劳动报酬是否成立，以及金额如何核算。")
    if "double_salary" in claim_types or facts.get("contract_signed") is False:
        issues.append("未签订书面劳动合同的责任承担及期间如何认定。")
    if "illegal_termination_damages" in claim_types:
        issues.append("解除或终止劳动关系是否违法，以及 2N 赔偿请求能否成立。")
    if "economic_compensation" in claim_types:
        issues.append("经济补偿或 N+1 方案的适用条件和金额如何认定。")
    if "year_end_bonus" in claim_types:
        issues.append("年终奖是否具有制度、合同或惯例依据，以及未发放理由能否成立。")
    if "overtime_pay" in claim_types:
        issues.append("加班事实、审批安排、调休情况和加班费金额如何认定。")
    if "unused_annual_leave_pay" in claim_types:
        issues.append("未休年休假天数、日工资基数和工资报酬如何核算。")
    issues.append("用户材料能否形成工资标准、劳动关系、拖欠事实和解除事实的证据链。")

    claims = _possible_claims(facts, amount_calculation)

    return {
        "case_summary_md": _paragraphs(case_summary),
        "issues_md": _numbered(issues),
        "claims_md": _numbered(claims),
        "legal_basis_md": _numbered(_legal_basis(evidence_pack)),
        "case_references_md": _numbered(_case_references(evidence_pack)),
        "amount_calculation_md": _numbered(_amount_lines(amount_calculation)),
        "evidence_list_md": _numbered(_evidence_lines(evidence_pack)),
        "risk_notice_md": _numbered(
            [
                "本报告为案情分析草稿，不替代律师意见或仲裁机构裁判结论。",
                "类案仅用于争议焦点和裁判倾向参考，不能作为本案法律依据直接引用。",
                "正式提交前需人工复核事实、证据原件、仲裁时效、管辖和金额计算。",
            ]
        ),
    }


def _possible_claims(facts: dict[str, Any], amount_calculation: dict[str, Any]) -> list[str]:
    claims_data = normalize_claims(facts.get("claims") or facts.get("expected_claims"))
    if not claims_data:
        claims_data = [{"type": "unpaid_salary", "label": CLAIM_DEFINITIONS["unpaid_salary"]["label"]}]
    amount_by_item = _amounts_by_item(amount_calculation)
    lines = []
    for claim in claims_data:
        claim_type = str(claim.get("type") or "")
        label = CLAIM_DEFINITIONS.get(claim_type, {}).get("label") or claim.get("label") or claim_type
        amount_key = f"custom.{claim.get('key')}" if claim_type == "custom" and claim.get("key") else claim_type
        amount = amount_by_item.get(amount_key)
        if amount not in (None, ""):
            lines.append(f"{label}：初步测算金额为人民币 {_money(amount)} 元。")
        else:
            lines.append(f"{label}：能否成立及金额需结合事实、证据和法律依据复核。")
    return lines


def _amounts_by_item(amount_calculation: dict[str, Any]) -> dict[str, Any]:
    amounts: dict[str, Any] = {}
    for item in amount_calculation.get("items") or []:
        if not isinstance(item, dict) or not item.get("item"):
            continue
        item_name = str(item.get("item"))
        if item_name == "custom" and item.get("key"):
            item_name = f"custom.{item['key']}"
        amounts[item_name] = item.get("amount")
    return amounts


def _legal_basis(evidence_pack: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for evidence in evidence_pack:
        if evidence.get("source_type") not in LEGAL_SOURCE_TYPES:
            continue
        anchor = evidence.get("citation_anchor")
        supported_claim = evidence.get("supported_claim") or evidence.get("quote")
        if not anchor or not supported_claim:
            continue
        line = f"{anchor}：{supported_claim}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines or ["待补充：需要从法律库检索可引用的法律、法规或规则依据。"]


def _case_references(evidence_pack: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for evidence in evidence_pack:
        if evidence.get("source_type") not in CASE_SOURCE_TYPES:
            continue
        metadata = _chunk_metadata(evidence)
        title = metadata.get("case_title") or evidence.get("source_name") or "未命名类案"
        issue = metadata.get("issue") or "争议焦点待复核"
        holding = metadata.get("holding") or evidence.get("supported_claim") or evidence.get("quote") or "裁判要旨待复核"
        result = metadata.get("result")
        source_url = evidence.get("source_url") or "无来源 URL"
        line = f"{title}：{issue}；参考要点：{holding}"
        if result:
            line += f"；处理结果：{result}"
        line += f"。来源：{source_url}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return lines or ["未检索到足够类案，本节需后续补充。"]


def _amount_lines(amount_calculation: dict[str, Any]) -> list[str]:
    if amount_calculation.get("status") != "calculated":
        return ["金额暂未完成测算，需补充工资标准、期间和相关事实。"]
    lines = []
    labels = {
        "unpaid_salary": "拖欠工资",
        "double_salary": "未签书面劳动合同二倍工资差额",
        "illegal_termination_damages": "违法解除劳动合同赔偿金",
        "economic_compensation": "解除劳动合同经济补偿",
        "year_end_bonus": "年终奖",
        "overtime_pay": "加班费",
        "unused_annual_leave_pay": "未休年休假工资报酬",
    }
    for item in amount_calculation.get("items") or []:
        label = labels.get(str(item.get("item")), str(item.get("item") or "测算项"))
        amount = item.get("amount")
        if amount is None:
            lines.append(f"{label}：待补充。")
            continue
        lines.append(f"{label}：人民币 {_money(amount)} 元。")
    notes = [str(note) for note in amount_calculation.get("notes") or [] if note]
    return [*lines, *notes] or ["金额暂未完成测算，需人工复核。"]


def _evidence_lines(evidence_pack: list[dict[str, Any]]) -> list[str]:
    lines = [
        "建议补充或核验：劳动合同/入职材料、工资流水、考勤记录、社保记录、解除通知、聊天记录、工牌或办公系统记录。"
    ]
    for evidence in evidence_pack:
        if evidence.get("source_type") != "user_material":
            continue
        source_name = evidence.get("source_name") or "用户上传材料"
        anchor = evidence.get("citation_anchor") or "未标注位置"
        quote = evidence.get("quote") or "内容待复核"
        lines.append(f"{source_name}（{anchor}）：{quote}")
    return lines


def _chunk_metadata(evidence: dict[str, Any]) -> dict[str, Any]:
    metadata = evidence.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    nested = metadata.get("metadata")
    return dict(nested) if isinstance(nested, dict) else {}


def _value(facts: dict[str, Any], key: str) -> str:
    value = facts.get(key)
    if value in (None, ""):
        return "待补充"
    return str(value)


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, start=1))


def _paragraphs(items: list[str]) -> str:
    return "\n\n".join(items)


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}"
