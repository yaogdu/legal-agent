# Legal Agent / 法律 Agent

[English README](README.en.md) | 中文/双语 README

Legal Agent 是一个自托管的劳动争议法律 Agent Demo。它从用户的一段自然语言描述开始，识别诉求和缺失事实，检索本地法律知识库，计算劳动争议相关金额，生成法律文书草稿，等待人工审批，并把每一步执行记录成可审计、可恢复的运行账本。

Legal Agent is a self-hosted labor-dispute legal-agent demo. It starts from a free-form user request, extracts claims and missing facts, retrieves local legal knowledge, calculates labor claims, drafts legal documents, waits for human approval, and records each step as an auditable and recoverable runtime ledger.

> 本项目是技术 Demo，不构成法律意见。  
> This project is a technical demo and does not provide legal advice.

## What It Does / 它能做什么

- 自然语言立案：用户先输入一段案情和诉求，不需要先填固定表单。  
  Natural-language intake: the user starts with a case description instead of a fixed form.
- 诉求驱动事实收集：系统根据识别出的诉求动态追问事实，例如 2N、年终奖、加班费、未休年假、自定义诉求等。  
  Claim-driven fact collection: the system asks dynamic follow-up questions based on extracted known or custom claims.
- Temporal 持久化调度：Workflow/Activity 失败后可以按 Temporal event history 重试未完成步骤。  
  Temporal-backed orchestration: workflows and activities can retry unfinished steps from durable event history.
- AgentLedger 业务账本：[AgentLedger](https://github.com/yaogdu/AgentLedger) 记录事实、工具调用、artifact、审批和审计回放数据。  
  AgentLedger business ledger: [AgentLedger](https://github.com/yaogdu/AgentLedger) records facts, tool calls, artifacts, approvals, and replay data.
- RAG 检索：本地法规、模板、案例、用户上传材料进入证据包。  
  RAG retrieval: local statutes, templates, cases, and uploaded user material are used as evidence packs.
- 人工审批：草稿可以先预览，再由用户审批后生成最终文档。  
  Human approval: drafts can be previewed before approval and final document generation.
- 自托管观测：可选 Langfuse 记录 LLM trace 和 generation。  
  Self-hosted observability: optional Langfuse tracing for LLM calls and generations.

## Architecture / 架构

![Legal Agent Architecture](docs/diagrams/legal_agent_architecture.svg)

核心链路：用户请求进入 FastAPI，API 创建业务 run 和 AgentLedger run，然后启动 Temporal Workflow。Temporal 调度 Activity，Worker 执行业务逻辑并把业务上下文写入 AgentLedger/Postgres。失败时 Temporal 负责重新调度未完成 Activity，Agent 代码用 `run_id` / `agentledger_run_id` 从 AgentLedger/Postgres 恢复业务上下文。

Core flow: a user request enters FastAPI. The API creates a business run and an AgentLedger run, then starts a Temporal workflow. Temporal schedules activities. Workers execute business logic and write business context to AgentLedger/Postgres. On failure, Temporal re-dispatches unfinished activities, while agent code restores business context from AgentLedger/Postgres using `run_id` and `agentledger_run_id`.

Detailed docs / 详细文档：

- [Usage / 使用方式](docs/USAGE.md)
- [Architecture / 架构原理](docs/ARCHITECTURE.md)
- [Operations / 运维启动](docs/OPERATIONS.md)
- [Runtime execution flow / 运行流程图](docs/diagrams/legal_agent_execution_flow.svg)
- [RAG architecture / RAG 架构图](docs/diagrams/legal_rag_architecture.svg)
- [Runtime governance / 运行治理图](docs/diagrams/legal_runtime_governance.svg)

## Quick Start / 快速启动

Prerequisites / 前置条件：

- Docker Desktop or Docker Engine with Compose.  
  安装 Docker Desktop 或 Docker Engine + Compose。
- A sibling checkout of AgentLedger at `../agent-runtime`.  
  需要在同级目录放置 AgentLedger 源码：`../agent-runtime`。
- AgentLedger upstream / AgentLedger 地址：<https://github.com/yaogdu/AgentLedger>

Start core services / 启动核心服务：

```bash
bash scripts/start.sh
```

Start full stack with Langfuse / 启动完整栈，包括 Langfuse：

```bash
bash scripts/start.sh full
```

Default URLs / 默认地址：

| Service / 服务 | URL |
| --- | --- |
| Agent UI / Agent 页面 | <http://localhost:28080/app/> |
| API docs / API 文档 | <http://localhost:28080/docs> |
| Temporal UI | <http://localhost:28088> |
| Langfuse | <http://localhost:3001> |

Lifecycle commands / 生命周期命令：

```bash
bash scripts/status.sh
bash scripts/restart.sh full
bash scripts/stop.sh all
```

The start script runs two replicas for each worker pool by default.  
启动脚本默认给每类 worker 跑 2 个副本：

- `legal-agent-worker=2`
- `rag-worker=2`
- `embedding-worker=2`

Override worker counts / 覆盖 worker 副本数：

```bash
LEGAL_AGENT_WORKERS=3 LEGAL_AGENT_RAG_WORKERS=2 LEGAL_AGENT_EMBEDDING_WORKERS=1 bash scripts/start.sh full
```

## Configuration / 配置

Keep local secrets in `.env`; it is ignored by Git. `.env.example` is the committed template.  
本地密钥放在 `.env`，该文件不会提交；`.env.example` 是提交到仓库的模板。

```bash
cp .env.example .env
```

LLM example / LLM 示例：

```bash
LEGAL_AGENT_LLM_ENABLED=true
LEGAL_AGENT_LLM_PROVIDER=openai_compatible
LEGAL_AGENT_LLM_BASE_URL=http://host.docker.internal:18080/v1
LEGAL_AGENT_LLM_API_KEY=replace-me
LEGAL_AGENT_LLM_MODEL=deepseek-v4-pro
LEGAL_AGENT_LLM_TIMEOUT_SECONDS=60
LEGAL_AGENT_LLM_TEMPERATURE=0.2
```

Langfuse example / Langfuse 示例：

```bash
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://langfuse-web:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PROJECT=legal-agent
```

## First Run / 第一次使用

Open the UI and submit a natural-language request.  
打开 UI，直接输入一段自然语言案情：

<http://localhost:28080/app/>

Example / 示例：

```text
我被公司无故解约，公司只同意给 N+1。我的诉求是 2N 赔偿、补发年终奖、加班费、未休年假补偿，并要求出具离职证明。
```

Typical flow / 典型流程：

1. Create a run / 创建 run。
2. Answer missing fact questions if the run waits for user input / 如进入事实补充阶段，按页面问题补充事实。
3. Wait for draft and review / 等待草稿和审查。
4. Preview draft before approval / 审批前预览草稿。
5. Approve or reject / 审批通过或拒绝。
6. Download final documents and inspect audit/replay / 下载最终文档，查看审计和回放。

See [Usage](docs/USAGE.md) for the full Web UI and API walkthrough.  
完整 Web UI 和 API 使用方式见 [Usage](docs/USAGE.md)。

## API Example / API 示例

Create a run / 创建 run：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs \
  -H 'Idempotency-Key: demo-run-001' \
  -H 'content-type: application/json' \
  -d '{"input":{"text":"我被公司无故辞退，诉求是 2N 赔偿、年终奖、加班费和未休年假补偿。"}}'
```

Check status / 查询状态：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}
```

Fetch draft before approval / 审批前获取草稿：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/draft
```

Fetch audit and replay / 查询审计和回放：

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/audit
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/replay
```

## Development / 开发

Seed the demo RAG corpus / 初始化 RAG 语料：

```bash
docker compose --profile jobs run --rm rag-ingest
```

Run smoke checks / 运行冒烟测试：

```bash
bash scripts/smoke_api.sh
```

Run offline evaluation / 运行离线评测：

```bash
bash scripts/eval_offline.sh
```

Project layout / 项目结构：

```text
src/legal_agent/api/                 FastAPI app and web UI mount / API 与 Web UI
src/legal_agent/workflows/           Temporal workflows, activities, workers / Temporal 工作流与 Worker
src/legal_agent/runtime/             AgentLedger adapter, health, tracing / AgentLedger 适配、健康检查、Tracing
src/legal_agent/core/                domain models, facts, claims, config / 领域模型、事实、诉求、配置
src/legal_agent/rag/                 RAG ingestion / RAG 导入
src/legal_agent/tools/               business tools called by activities / Activity 调用的业务工具
src/legal_agent/document_templates/  Markdown/DOCX document rendering / 文书渲染
rag/                                 seed legal corpus and source manifests / 法律语料和来源清单
migrations/                          PostgreSQL schema migrations / 数据库迁移
docs/                                architecture and operations docs / 架构和运维文档
scripts/                             local lifecycle and smoke scripts / 本地启动停止和测试脚本
```

## License / 许可证

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).

本项目使用 Apache License 2.0 开源许可。详见 [LICENSE](LICENSE)。
