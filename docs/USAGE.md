# Usage / 使用方式

This document explains how to use Legal Agent from the Web UI and from HTTP APIs.  
本文说明如何通过 Web UI 和 HTTP API 使用 Legal Agent。

## Start the Stack / 启动服务

Start the complete local stack, including Langfuse:

启动完整本地栈，包括 Langfuse：

```bash
bash scripts/start.sh full
```

Open:

打开：

- Agent UI / Agent 页面：<http://localhost:28080/app/>
- API docs / API 文档：<http://localhost:28080/docs>
- Temporal UI：<http://localhost:28088>
- Langfuse：<http://localhost:3001>

Check service health:

检查服务健康状态：

```bash
bash scripts/status.sh
curl -fsS http://localhost:28080/healthz/details
```

## Web UI Flow / 页面使用流程

### 1. Submit a Case Description / 提交案情描述

On the first screen, enter a free-form description. Do not start by filling every legal fact manually. The agent is designed to infer claims first and then ask for missing facts.

在第一页直接输入一段自然语言案情，不需要一开始手工填完所有法律事实。系统会先识别诉求，再动态追问缺失事实。

Example:

示例：

```text
我被公司无故解约，公司只同意给 N+1。我的诉求是 2N 赔偿、补发年终奖、加班费、未休年假补偿，并要求出具离职证明。
```

The API creates:

API 会创建：

- `run_id`: business run id / 业务 run 标识；
- `agentledger_run_id`: AgentLedger run id / AgentLedger 运行账本标识；
- Temporal workflow id, derived from `run_id` / 基于 `run_id` 生成的 Temporal workflow id。

### 2. Answer Missing Facts / 补充缺失事实

If the run enters `WAITING_USER_INPUT`, the page shows grouped questions. These questions come from the detected claims.

如果 run 进入 `WAITING_USER_INPUT`，页面会展示分组问题。这些问题来自已经识别出的诉求。

Examples:

示例：

- If the claim is 2N illegal termination damages, the agent asks for employment dates, salary, termination reason, company offer, and termination notice.
  如果诉求是 2N 违法解除赔偿，系统会追问入离职时间、工资、解除原因、公司方案、解除通知等。
- If the claim is year-end bonus, the agent asks for bonus amount, bonus basis, and payment status.
  如果诉求是年终奖，系统会追问年终奖金额、发放依据、发放情况。
- If the claim is custom, the agent creates custom fields for basis, amount, and evidence.
  如果是自定义诉求，系统会生成该诉求的依据、金额、证据字段。

After you submit facts, the UI sends them to the same run and Temporal receives a `submit_facts` signal.

提交事实后，页面会把事实合并到同一个 run，Temporal 会收到 `submit_facts` signal。

### 3. Wait for Draft and Review / 等待草稿与审查

After facts are sufficient, the workflow continues through:

事实足够后，Workflow 会继续执行：

```text
PLAN -> RETRIEVE -> TOOL -> DRAFT -> REVIEW -> APPROVAL
```

During these steps:

这些步骤里：

- `PLAN` builds the execution plan / 生成执行计划；
- `RETRIEVE` searches legal/user material / 检索法规、案例、模板和用户材料；
- `TOOL` runs deterministic tools such as claim calculation / 执行金额计算等确定性工具；
- `DRAFT` creates the draft document / 生成文书草稿；
- `REVIEW` checks citations, format, and risk / 检查引用、格式和风险；
- `APPROVAL` creates a human approval request / 创建人工审批请求。

### 4. Preview and Approve / 预览并审批

When the run reaches `WAITING_APPROVAL`, open the Approval tab. The draft preview should be available before final approval.

当 run 到达 `WAITING_APPROVAL`，打开 Approval 标签页。审批前应该可以预览草稿。

Approve only after checking:

审批前建议检查：

- facts and claims / 事实和诉求；
- requested amounts / 请求金额；
- legal basis and citations / 法律依据和引用；
- document structure / 文书结构；
- obvious hallucinations or unsupported statements / 明显幻觉或无依据表述。

After approval, Temporal receives a `submit_approval` signal and continues to `OUTPUT`.

审批通过后，Temporal 会收到 `submit_approval` signal，并继续执行 `OUTPUT`。

### 5. Download Documents and Inspect Audit / 下载文档并查看审计

After `COMPLETED`, the UI can show generated documents. You can also call the document endpoints directly.

`COMPLETED` 后，页面可以展示生成文档，也可以直接调用文档接口。

AgentLedger audit/replay is useful when you need to explain what happened in a run.

如果需要解释一次 run 发生了什么，可以查看 AgentLedger audit/replay。

## HTTP API Flow / HTTP API 使用流程

### Optional File Upload / 可选上传材料

Upload Markdown/text/DOCX material:

上传 Markdown、文本或 DOCX 材料：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/files \
  -H 'Idempotency-Key: demo-upload-001' \
  -F "file=@./evidence.md;type=text/markdown"
```

Use the returned `file_id` in the create-run request.

把返回的 `file_id` 放入创建 run 请求。

### Create Run / 创建 Run

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs \
  -H 'Idempotency-Key: demo-run-001' \
  -H 'content-type: application/json' \
  -d '{
    "task_type": "document_generation",
    "legal_domain": "labor_dispute",
    "jurisdiction": "CN-BJ",
    "input": {
      "text": "我被公司无故辞退，诉求是 2N 赔偿、年终奖、加班费和未休年假补偿。",
      "file_ids": []
    },
    "output_options": {
      "document_type": "labor_arbitration_application",
      "format": "markdown",
      "require_human_review": true
    }
  }'
```

### Poll Status / 查询状态

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}
```

Important response fields:

重要响应字段：

| Field / 字段 | Meaning / 含义 |
| --- | --- |
| `run_status` | Overall run status, such as `RUNNING`, `WAITING_USER_INPUT`, `WAITING_APPROVAL`, `COMPLETED`. / 整体状态。 |
| `current_node` | Business node, such as `DRAFT` or `APPROVAL`. / 当前业务节点。 |
| `missing_fields` | Fact fields still required. / 仍需补充的事实字段。 |
| `question_groups` | User-facing grouped questions. / 面向用户的问题分组。 |
| `requires_user_input` | Whether facts must be submitted. / 是否需要补充事实。 |
| `requires_approval` | Whether approval must be submitted. / 是否需要审批。 |

### Submit Missing Facts / 提交缺失事实

Only call this when `requires_user_input=true`.

仅当 `requires_user_input=true` 时调用。

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/facts \
  -H 'Idempotency-Key: demo-facts-001' \
  -H 'content-type: application/json' \
  -d '{
    "facts": {
      "applicant_name": "张三",
      "company_name": "某某科技有限公司",
      "work_start_date": "2022-03-01",
      "work_end_date": "2025-05-01",
      "monthly_salary": 30000,
      "termination_reason": "公司单方解除，理由不明确",
      "company_offer": "N+1",
      "requested_termination_compensation": "2N",
      "termination_notice": "已收到书面解除通知",
      "year_end_bonus_amount": 60000,
      "year_end_bonus_basis": "劳动合同和历史发放记录",
      "year_end_bonus_paid": "未发",
      "overtime_hours": 120,
      "rest_day_overtime_hours": 40,
      "statutory_holiday_overtime_hours": 8,
      "overtime_period": "2024-01 至 2025-04",
      "overtime_approval": "有考勤和聊天记录",
      "annual_leave_entitlement_days": 10,
      "annual_leave_taken_days": 2,
      "daily_wage": 1379.31,
      "evidence_available": ["工资流水", "解除通知", "聊天记录", "考勤记录"]
    }
  }'
```

### Preview Draft / 预览草稿

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/draft
```

This endpoint is useful before approving the final document.

该接口用于审批前查看草稿。

### Approve or Reject / 审批或拒绝

List approval requests:

查询审批请求：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/approvals
```

Approve:

审批通过：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/approvals/{approval_id} \
  -H 'Idempotency-Key: demo-approval-001' \
  -H 'content-type: application/json' \
  -d '{"approved":true,"approver":"demo-reviewer","reason":"reviewed"}'
```

Reject:

审批拒绝：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/approvals/{approval_id} \
  -H 'Idempotency-Key: demo-approval-reject-001' \
  -H 'content-type: application/json' \
  -d '{"approved":false,"approver":"demo-reviewer","reason":"draft needs correction"}'
```

### Download Documents / 下载文档

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/documents
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/documents/{document_id}/markdown
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/documents/{document_id}/docx --output document.docx
```

### Audit and Replay / 审计和回放

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/audit
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/replay
```

`audit` returns the business run, AgentLedger events, tool ledger rows, artifacts, approvals, and summary.  
`audit` 返回业务 run、AgentLedger events、tool ledger、artifacts、approvals 和 summary。

`replay` returns a timeline-oriented view for debugging and explanation.  
`replay` 返回面向时间线的调试和解释视图。

## Status Guide / 状态说明

| Status / 状态 | What it means / 含义 | User action / 用户动作 |
| --- | --- | --- |
| `CREATED` | Run has been created. / run 已创建。 | Wait. / 等待。 |
| `RUNNING` | Workflow is executing activities. / Workflow 正在执行 Activity。 | Wait or inspect logs. / 等待或查看日志。 |
| `WAITING_USER_INPUT` | More facts are required. / 需要补充事实。 | Submit missing facts. / 提交缺失事实。 |
| `WAITING_APPROVAL` | Draft is ready for human review. / 草稿等待人工审批。 | Preview and approve/reject. / 预览并审批或拒绝。 |
| `COMPLETED` | Final artifacts are generated. / 最终 artifact 已生成。 | Download documents. / 下载文档。 |
| `APPROVAL_REJECTED` | Reviewer rejected the approval request. / 审批被拒绝。 | Start a new run or inspect audit. / 新建 run 或查看审计。 |
| `EXPIRED` | User input or approval timed out. / 补充事实或审批超时。 | Start a new run if needed. / 必要时新建 run。 |
| `CANCELLED` | Run was cancelled. / run 已取消。 | No further action. / 无需继续。 |
| `FAILED` | Execution failed. / 执行失败。 | Inspect API logs, Temporal UI, audit. / 查看 API 日志、Temporal UI、审计。 |

## Idempotency / 幂等

Write endpoints require `Idempotency-Key`. Reusing the same key with the same request returns the original response. Reusing it with a different request returns `409`.

写接口需要 `Idempotency-Key`。同一个 key 搭配同一个请求会返回原响应；同一个 key 搭配不同请求会返回 `409`。

This applies to:

适用于：

- create run / 创建 run；
- upload file / 上传文件；
- submit facts / 提交事实；
- approve/reject / 审批；
- cancel run / 取消 run。

## Troubleshooting During Use / 使用时排查

- If input text disappears in the UI, check whether the page is polling while editing; current UI should preserve editable fields during polling.
  如果页面输入被刷新掉，检查是否在编辑时轮询状态；当前 UI 应该在轮询时保留编辑中的字段。
- If the run stays at `DRAFT`, check LLM configuration and `legal-agent-worker` logs.
  如果 run 卡在 `DRAFT`，检查 LLM 配置和 `legal-agent-worker` 日志。
- If approval has no draft preview, call `/draft` and inspect API logs.
  如果审批页没有草稿预览，调用 `/draft` 并查看 API 日志。
- If a document is not generated after approval, check Temporal UI for the `output_activity` state.
  如果审批后没有生成文档，去 Temporal UI 检查 `output_activity` 状态。
