from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    env: str
    api_host: str
    api_port: int
    database_dsn: str
    agentledger_postgres_dsn: str
    agentledger_postgres_schema: str
    agentledger_blob_dir: Path
    data_dir: Path
    rag_source_manifest: Path
    rag_seed_file: Path
    temporal_address: str
    temporal_namespace: str
    temporal_task_queue: str
    temporal_rag_task_queue: str
    temporal_embedding_task_queue: str
    temporal_start_workflows: bool
    temporal_search_attributes_enabled: bool
    user_input_timeout_seconds: int
    approval_timeout_seconds: int
    langfuse_enabled: bool
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_project: str
    llm_enabled: bool
    llm_provider: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    llm_temperature: float


def load_settings() -> Settings:
    return Settings(
        env=os.getenv("LEGAL_AGENT_ENV", "dev"),
        api_host=os.getenv("LEGAL_AGENT_API_HOST", "0.0.0.0"),
        api_port=int(os.getenv("LEGAL_AGENT_API_PORT", "8080")),
        database_dsn=os.getenv("DATABASE_DSN", "postgresql://legal_agent:legal_agent@localhost:25432/legal_agent"),
        agentledger_postgres_dsn=os.getenv("AGENTLEDGER_POSTGRES_DSN", os.getenv("DATABASE_DSN", "")),
        agentledger_postgres_schema=os.getenv("AGENTLEDGER_POSTGRES_SCHEMA", "agentledger"),
        agentledger_blob_dir=Path(os.getenv("AGENTLEDGER_BLOB_DIR", ".data/agentledger/blobs")),
        data_dir=Path(os.getenv("LEGAL_AGENT_DATA_DIR", ".data/legal-agent")),
        rag_source_manifest=Path(os.getenv("RAG_SOURCE_MANIFEST", "rag/labor_dispute_sources.yaml")),
        rag_seed_file=Path(os.getenv("RAG_SEED_FILE", "rag/seeds/labor_dispute_minimal.json")),
        temporal_address=os.getenv("TEMPORAL_ADDRESS", "localhost:7233"),
        temporal_namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
        temporal_task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "legal-agent-worker-dev"),
        temporal_rag_task_queue=os.getenv("TEMPORAL_RAG_TASK_QUEUE", "legal-agent-rag-worker-dev"),
        temporal_embedding_task_queue=os.getenv("TEMPORAL_EMBEDDING_TASK_QUEUE", "legal-agent-embedding-worker-dev"),
        temporal_start_workflows=_bool_env("TEMPORAL_START_WORKFLOWS", True),
        temporal_search_attributes_enabled=_bool_env("TEMPORAL_SEARCH_ATTRIBUTES_ENABLED", True),
        user_input_timeout_seconds=int(os.getenv("LEGAL_AGENT_USER_INPUT_TIMEOUT_SECONDS", "86400")),
        approval_timeout_seconds=int(os.getenv("LEGAL_AGENT_APPROVAL_TIMEOUT_SECONDS", "86400")),
        langfuse_enabled=_bool_env("LANGFUSE_ENABLED", False),
        langfuse_host=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
        langfuse_public_key=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        langfuse_secret_key=os.getenv("LANGFUSE_SECRET_KEY", ""),
        langfuse_project=os.getenv("LANGFUSE_PROJECT", "legal-agent"),
        llm_enabled=_bool_env("LEGAL_AGENT_LLM_ENABLED", False),
        llm_provider=os.getenv("LEGAL_AGENT_LLM_PROVIDER", "openai_compatible"),
        llm_base_url=os.getenv("LEGAL_AGENT_LLM_BASE_URL", ""),
        llm_api_key=os.getenv("LEGAL_AGENT_LLM_API_KEY", ""),
        llm_model=os.getenv("LEGAL_AGENT_LLM_MODEL", ""),
        llm_timeout_seconds=float(os.getenv("LEGAL_AGENT_LLM_TIMEOUT_SECONDS", "60")),
        llm_temperature=float(os.getenv("LEGAL_AGENT_LLM_TEMPERATURE", "0.2")),
    )
