# Legal Agent

[中文/双语 README](README.md) | English README

Legal Agent is a self-hosted labor-dispute legal-agent demo. It starts from a free-form user request, extracts claims and missing facts, retrieves local legal knowledge, calculates labor claims, drafts legal documents, waits for human approval, and records each step as an auditable and recoverable runtime ledger.

> This project is a technical demo and does not provide legal advice.

## What It Does

- Natural-language intake: users start with a case description instead of a fixed form.
- Claim-driven fact collection: the system asks dynamic follow-up questions based on extracted known or custom claims.
- Temporal-backed orchestration: workflows and activities can retry unfinished steps from durable event history.
- AgentLedger business ledger: [AgentLedger](https://github.com/yaogdu/AgentLedger) records facts, tool calls, artifacts, approvals, and replay data.
- RAG retrieval: local statutes, templates, cases, and uploaded user material are used as evidence packs.
- Human approval: drafts can be previewed before approval and final document generation.
- Self-hosted observability: optional Langfuse tracing for LLM calls and generations.

## Architecture

![Legal Agent Architecture](docs/diagrams/legal_agent_architecture.svg)

Core flow: a user request enters FastAPI. The API creates a business run and an AgentLedger run, then starts a Temporal workflow. Temporal schedules activities. Workers execute business logic and write business context to AgentLedger/Postgres. On failure, Temporal re-dispatches unfinished activities, while agent code restores business context from AgentLedger/Postgres using `run_id` and `agentledger_run_id`.

Detailed docs:

- [Usage](docs/USAGE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Operations](docs/OPERATIONS.md)
- [Runtime execution flow](docs/diagrams/legal_agent_execution_flow.svg)
- [RAG architecture](docs/diagrams/legal_rag_architecture.svg)
- [Runtime governance](docs/diagrams/legal_runtime_governance.svg)

## Quick Start

Prerequisites:

- Docker Desktop or Docker Engine with Compose.
- A sibling checkout of AgentLedger at `../agent-runtime`.
- AgentLedger upstream: <https://github.com/yaogdu/AgentLedger>

Start core services:

```bash
bash scripts/start.sh
```

Start the full stack, including Langfuse:

```bash
bash scripts/start.sh full
```

Default URLs:

| Service | URL |
| --- | --- |
| Agent UI | <http://localhost:28080/app/> |
| API docs | <http://localhost:28080/docs> |
| Temporal UI | <http://localhost:28088> |
| Langfuse | <http://localhost:3001> |

Lifecycle commands:

```bash
bash scripts/status.sh
bash scripts/restart.sh full
bash scripts/stop.sh all
```

The start script runs two replicas for each worker pool by default:

- `legal-agent-worker=2`
- `rag-worker=2`
- `embedding-worker=2`

Override worker counts:

```bash
LEGAL_AGENT_WORKERS=3 LEGAL_AGENT_RAG_WORKERS=2 LEGAL_AGENT_EMBEDDING_WORKERS=1 bash scripts/start.sh full
```

## Configuration

Keep local secrets in `.env`; it is ignored by Git. `.env.example` is the committed template.

```bash
cp .env.example .env
```

LLM example:

```bash
LEGAL_AGENT_LLM_ENABLED=true
LEGAL_AGENT_LLM_PROVIDER=openai_compatible
LEGAL_AGENT_LLM_BASE_URL=http://host.docker.internal:18080/v1
LEGAL_AGENT_LLM_API_KEY=replace-me
LEGAL_AGENT_LLM_MODEL=deepseek-v4-pro
LEGAL_AGENT_LLM_TIMEOUT_SECONDS=60
LEGAL_AGENT_LLM_TEMPERATURE=0.2
```

Langfuse example:

```bash
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://langfuse-web:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PROJECT=legal-agent
```

## First Run

Open the UI and submit a natural-language request:

<http://localhost:28080/app/>

Example:

```text
我被公司无故解约，公司只同意给 N+1。我的诉求是 2N 赔偿、补发年终奖、加班费、未休年假补偿，并要求出具离职证明。
```

Typical flow:

1. Create a run.
2. Answer missing fact questions if the run waits for user input.
3. Wait for draft and review.
4. Preview draft before approval.
5. Approve or reject.
6. Download final documents and inspect audit/replay.

See [Usage](docs/USAGE.md) for the full Web UI and API walkthrough.

## API Example

Create a run:

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs \
  -H 'Idempotency-Key: demo-run-001' \
  -H 'content-type: application/json' \
  -d '{"input":{"text":"我被公司无故辞退，诉求是 2N 赔偿、年终奖、加班费和未休年假补偿。"}}'
```

Check status:

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}
```

Fetch draft before approval:

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/draft
```

Fetch audit and replay:

```bash
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/audit
curl -s http://localhost:28080/api/v1/legal-agent/runs/{run_id}/replay
```

## Development

Seed the demo RAG corpus:

```bash
docker compose --profile jobs run --rm rag-ingest
```

Run smoke checks:

```bash
bash scripts/smoke_api.sh
```

Run offline evaluation:

```bash
bash scripts/eval_offline.sh
```

Project layout:

```text
src/legal_agent/api/                 FastAPI app and web UI mount
src/legal_agent/workflows/           Temporal workflows, activities, workers
src/legal_agent/runtime/             AgentLedger adapter, health, tracing
src/legal_agent/core/                domain models, facts, claims, config
src/legal_agent/rag/                 RAG ingestion
src/legal_agent/tools/               business tools called by activities
src/legal_agent/document_templates/  Markdown/DOCX document rendering
rag/                                 seed legal corpus and source manifests
migrations/                          PostgreSQL schema migrations
docs/                                architecture and operations docs
scripts/                             local lifecycle and smoke scripts
```

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE).
