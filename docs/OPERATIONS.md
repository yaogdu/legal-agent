# Operations / 运维与本地运行

This document covers local development and demo operations. It is not a full production runbook, but the service boundaries match the intended production architecture.

本文说明本地开发和 Demo 运行方式。它不是完整生产运维手册，但服务边界与目标生产架构一致。

## Services / 服务列表

| Service / 服务 | Purpose / 用途 |
| --- | --- |
| `postgres` | Application database, pgvector RAG storage, AgentLedger tables, Langfuse schema in local mode. / 应用数据库、pgvector RAG、AgentLedger 表、本地 Langfuse schema。 |
| `temporal` | Local Temporal server using the auto-setup image. / 使用 auto-setup 镜像的本地 Temporal。 |
| `temporal-ui` | Temporal web UI. / Temporal 页面。 |
| `legal-agent-api` | FastAPI API and embedded demo web UI. / FastAPI API 和内置 Demo 页面。 |
| `legal-agent-worker` | Main Temporal worker pool. / 主 Temporal Worker 池。 |
| `rag-worker` | Retrieval worker pool. / RAG 检索 Worker 池。 |
| `embedding-worker` | Embedding backfill worker pool. / Embedding 补齐 Worker 池。 |
| `redis` | Langfuse queue/cache dependency when `full` profile is used. / `full` 模式下的 Langfuse 队列/缓存依赖。 |
| `clickhouse` | Langfuse analytics store when `full` profile is used. / `full` 模式下的 Langfuse 分析存储。 |
| `minio` | S3-compatible local object store for Langfuse, backed by the shared Docker volume. / Langfuse 使用的本地 S3 兼容对象存储，数据挂在共享 Docker volume。 |
| `langfuse-web` | Langfuse web/API service. / Langfuse Web/API 服务。 |
| `langfuse-worker` | Langfuse background worker. / Langfuse 后台 Worker。 |

## Lifecycle Commands / 生命周期命令

Start core services:

启动核心服务：

```bash
bash scripts/start.sh
```

Start core services plus Langfuse:

启动核心服务和 Langfuse：

```bash
bash scripts/start.sh full
```

Stop only agent containers:

只停止 Agent 相关容器：

```bash
bash scripts/stop.sh core
```

Stop the whole local stack:

停止整个本地栈：

```bash
bash scripts/stop.sh all
```

Restart:

重启：

```bash
bash scripts/restart.sh full
```

Check status:

检查状态：

```bash
bash scripts/status.sh
```

Default worker replica counts:

默认 Worker 副本数：

| Variable / 变量 | Default / 默认值 |
| --- | --- |
| `LEGAL_AGENT_WORKERS` | `2` |
| `LEGAL_AGENT_RAG_WORKERS` | `2` |
| `LEGAL_AGENT_EMBEDDING_WORKERS` | `2` |

Override example:

覆盖示例：

```bash
LEGAL_AGENT_WORKERS=3 LEGAL_AGENT_RAG_WORKERS=2 LEGAL_AGENT_EMBEDDING_WORKERS=1 bash scripts/start.sh full
```

## URLs / 服务地址

| Service / 服务 | URL |
| --- | --- |
| Agent UI / Agent 页面 | <http://localhost:28080/app/> |
| API docs / API 文档 | <http://localhost:28080/docs> |
| API health / 健康检查 | <http://localhost:28080/healthz/details> |
| API metrics / 指标 | <http://localhost:28080/metrics> |
| Temporal UI | <http://localhost:28088> |
| Langfuse | <http://localhost:3001> |

Langfuse starts without a project-specific API key. Open the Langfuse page, complete its self-hosted onboarding/login flow, create a project, and create API keys.

Langfuse 不会自带项目 API Key。打开 Langfuse 页面，完成 self-hosted 初始化/登录，创建项目并生成 API Key。

## Environment / 环境变量

Keep secrets in `.env`; it is ignored by Git. `.env.example` is committed as the default configuration template.

密钥放在 `.env`；该文件已加入 `.gitignore`。`.env.example` 是提交到仓库的默认配置模板。

Important variables:

重要变量：

| Variable / 变量 | Purpose / 用途 |
| --- | --- |
| `DATABASE_DSN` | Application PostgreSQL connection string. / 应用数据库连接串。 |
| `AGENTLEDGER_POSTGRES_DSN` | AgentLedger PostgreSQL connection string. / AgentLedger 数据库连接串。 |
| `AGENTLEDGER_BLOB_DIR` | Blob artifact storage path. / Blob artifact 存储路径。 |
| `LEGAL_AGENT_DATA_DIR` | Application data path. / 应用数据路径。 |
| `TEMPORAL_ADDRESS` | Temporal server address. / Temporal 服务地址。 |
| `TEMPORAL_TASK_QUEUE` | Main worker queue. / 主 Worker 队列。 |
| `TEMPORAL_RAG_TASK_QUEUE` | RAG worker queue. / RAG Worker 队列。 |
| `TEMPORAL_EMBEDDING_TASK_QUEUE` | Embedding worker queue. / Embedding Worker 队列。 |
| `LANGFUSE_ENABLED` | Enables Langfuse tracing when true. / 是否启用 Langfuse tracing。 |
| `LEGAL_AGENT_LLM_ENABLED` | Enables LLM calls when true. / 是否启用 LLM 调用。 |

LLM example:

LLM 示例：

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

Langfuse 示例：

```bash
LANGFUSE_ENABLED=true
LANGFUSE_HOST=http://langfuse-web:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_PROJECT=legal-agent
```

## Data and Artifacts / 数据与 Artifact

Local Docker named volumes:

本地 Docker named volumes：

- `postgres-data`: PostgreSQL database files / PostgreSQL 数据库文件。
- `shared-data`: AgentLedger blobs, legal-agent artifacts, Langfuse MinIO data / AgentLedger blob、legal-agent artifact、Langfuse MinIO 数据。
- `clickhouse-data`: Langfuse ClickHouse data / Langfuse ClickHouse 数据。
- `clickhouse-logs`: ClickHouse logs / ClickHouse 日志。

Inside containers:

容器内路径：

- AgentLedger blobs: `/data/agentledger/blobs`
- Legal-agent data: `/data/legal-agent`
- Langfuse MinIO path: `/minio-data/langfuse`

The current local setup uses Docker volumes rather than a visible MinIO browser. MinIO is still present as an S3-compatible service for Langfuse, but its console is disabled.

当前本地配置使用 Docker volume，不依赖可见的 MinIO 控制台。MinIO 仍作为 Langfuse 的 S3 兼容服务存在，但控制台关闭。

## RAG / 检索语料

Seed the local legal corpus:

初始化本地法律语料：

```bash
docker compose --profile jobs run --rm rag-ingest
```

Source files:

源文件：

- `rag/labor_dispute_sources.yaml`
- `rag/seeds/labor_dispute_minimal.json`
- `rag/library/labor_dispute/*.md`
- `rag/library/labor_dispute/chunk_metadata.yaml`

The demo uses local snapshots and does not fetch official legal pages during every run.

Demo 使用本地快照，不会在每次 run 时访问官方网页。

## Health and Troubleshooting / 健康检查与排查

Readiness check:

就绪检查：

```bash
curl -fsS http://localhost:28080/healthz/details
```

Container status:

容器状态：

```bash
docker compose ps
```

Recent logs:

最近日志：

```bash
docker compose logs --tail=100 legal-agent-api
docker compose logs --tail=100 legal-agent-worker
docker compose logs --tail=100 langfuse-web
```

Common checks:

常见检查：

- If LLM calls fail from Docker, use `host.docker.internal` instead of `127.0.0.1` for services running on the host.  
  如果 Docker 容器访问宿主机 LLM 失败，用 `host.docker.internal`，不要用容器内的 `127.0.0.1`。
- If Langfuse ClickHouse migrations mention a missing ZooKeeper cluster config, keep `CLICKHOUSE_CLUSTER_ENABLED=false` for the local single-node stack.  
  如果 Langfuse ClickHouse migration 报 ZooKeeper cluster 配置缺失，本地单节点保持 `CLICKHOUSE_CLUSTER_ENABLED=false`。
- If a run waits at fact collection, submit missing facts through the UI or `/facts` endpoint.  
  如果 run 等待事实补充，通过 UI 或 `/facts` 接口提交事实。
- If a run waits at approval, preview the draft in the UI or `/draft`, then submit approval.  
  如果 run 等待审批，通过 UI 或 `/draft` 预览草稿，再提交审批。
- If a worker crashes mid-step, check Temporal UI for activity retries and AgentLedger audit endpoints for business context.  
  如果 Worker 在中途挂掉，去 Temporal UI 看 Activity 重试，并通过 AgentLedger audit 接口查看业务上下文。

## Evaluation and Smoke Tests / 评测与冒烟测试

Smoke test:

冒烟测试：

```bash
bash scripts/smoke_api.sh
```

Offline quality gate:

离线质量门禁：

```bash
bash scripts/eval_offline.sh
```

Reports are written under `evaluation-reports/`, which is ignored by Git.

报告会写入 `evaluation-reports/`，该目录已加入 `.gitignore`。

## Production Notes / 生产化说明

The local stack is intentionally close to a production topology, but it is not a production deployment as-is.

当前本地栈刻意接近生产拓扑，但不能原样当生产部署。

Production changes usually include:

生产化通常需要：

- replace `temporalio/auto-setup` with a real multi-service Temporal deployment;  
  用正式多服务 Temporal 部署替换 `temporalio/auto-setup`；
- use managed or HA PostgreSQL;  
  使用托管或高可用 PostgreSQL；
- use HA ClickHouse/Redis/object storage for Langfuse;  
  为 Langfuse 使用高可用 ClickHouse、Redis、对象存储；
- move secrets to a secret manager;  
  把密钥移入密钥管理系统；
- add backups, retention, metrics, alerts, and runbooks.  
  增加备份、保留策略、指标、告警和运维手册。
