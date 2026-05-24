CREATE SCHEMA IF NOT EXISTS legal_agent;
CREATE SCHEMA IF NOT EXISTS rag;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS legal_agent.legal_agent_run (
  id BIGSERIAL PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL UNIQUE,
  agentledger_run_id VARCHAR(128) NOT NULL UNIQUE,
  request_id VARCHAR(128) NOT NULL,
  session_id VARCHAR(128),
  temporal_workflow_id VARCHAR(256),
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  task_type VARCHAR(64) NOT NULL,
  legal_domain VARCHAR(64) NOT NULL,
  jurisdiction VARCHAR(32) NOT NULL DEFAULT 'CN',
  risk_level VARCHAR(32) NOT NULL,
  run_status VARCHAR(32) NOT NULL,
  current_node VARCHAR(64),
  current_node_status VARCHAR(32),
  input_json JSONB NOT NULL,
  facts_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  missing_fields_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  result_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_legal_agent_run_status ON legal_agent.legal_agent_run(run_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_legal_agent_run_tenant ON legal_agent.legal_agent_run(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS legal_agent.api_idempotency_key (
  id BIGSERIAL PRIMARY KEY,
  scope VARCHAR(256) NOT NULL,
  idempotency_key VARCHAR(256) NOT NULL,
  request_hash VARCHAR(128) NOT NULL,
  state VARCHAR(32) NOT NULL,
  response_json JSONB,
  status_code INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(scope, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_api_idempotency_key_updated ON legal_agent.api_idempotency_key(updated_at DESC);

CREATE TABLE IF NOT EXISTS legal_agent.legal_agent_fact (
  id BIGSERIAL PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL REFERENCES legal_agent.legal_agent_run(run_id) ON DELETE CASCADE,
  fact_key VARCHAR(128) NOT NULL,
  fact_value TEXT,
  normalized_value JSONB,
  source_type VARCHAR(32) NOT NULL,
  source_ref VARCHAR(256),
  confidence NUMERIC(6,4),
  status VARCHAR(32) NOT NULL DEFAULT 'confirmed',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id, fact_key)
);

CREATE TABLE IF NOT EXISTS legal_agent.uploaded_file (
  id BIGSERIAL PRIMARY KEY,
  file_id VARCHAR(64) NOT NULL UNIQUE,
  tenant_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(64) NOT NULL,
  original_filename TEXT NOT NULL,
  content_type TEXT,
  size_bytes BIGINT NOT NULL,
  sha256 VARCHAR(64) NOT NULL,
  storage_path TEXT NOT NULL,
  parse_status VARCHAR(32) NOT NULL,
  text_preview TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_uploaded_file_tenant ON legal_agent.uploaded_file(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_uploaded_file_sha256 ON legal_agent.uploaded_file(sha256);

CREATE TABLE IF NOT EXISTS legal_agent.uploaded_file_chunk (
  id BIGSERIAL PRIMARY KEY,
  chunk_id VARCHAR(96) NOT NULL UNIQUE,
  file_id VARCHAR(64) NOT NULL REFERENCES legal_agent.uploaded_file(file_id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  page_no INTEGER,
  content TEXT NOT NULL,
  citation_anchor VARCHAR(256) NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(file_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_uploaded_file_chunk_file ON legal_agent.uploaded_file_chunk(file_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_uploaded_file_chunk_tsv ON legal_agent.uploaded_file_chunk USING GIN(content_tsv);

CREATE TABLE IF NOT EXISTS rag.rag_ingest_run (
  id BIGSERIAL PRIMARY KEY,
  ingest_id VARCHAR(64) NOT NULL UNIQUE,
  domain VARCHAR(64) NOT NULL,
  source_manifest TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  stats_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS rag.legal_source_document (
  id BIGSERIAL PRIMARY KEY,
  source_id VARCHAR(128) NOT NULL,
  doc_id VARCHAR(128) NOT NULL UNIQUE,
  doc_type VARCHAR(64) NOT NULL,
  authority_level VARCHAR(16) NOT NULL,
  title TEXT NOT NULL,
  source_url TEXT NOT NULL,
  jurisdiction VARCHAR(32) NOT NULL,
  issuing_authority TEXT,
  document_no VARCHAR(128),
  effective_from DATE,
  effective_to DATE,
  retrieved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  version_hash VARCHAR(128) NOT NULL,
  status VARCHAR(32) NOT NULL,
  snapshot_path TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rag.legal_document_chunk (
  id BIGSERIAL PRIMARY KEY,
  chunk_id VARCHAR(64) NOT NULL UNIQUE,
  doc_id VARCHAR(128) NOT NULL REFERENCES rag.legal_source_document(doc_id) ON DELETE CASCADE,
  doc_type VARCHAR(64) NOT NULL,
  authority_level VARCHAR(16) NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata_json JSONB NOT NULL,
  citation_anchor VARCHAR(256),
  effective_from DATE,
  effective_to DATE,
  jurisdiction VARCHAR(32) NOT NULL,
  embedding vector(1024),
  content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_legal_document_chunk_doc_id ON rag.legal_document_chunk(doc_id);
CREATE INDEX IF NOT EXISTS idx_legal_document_chunk_meta ON rag.legal_document_chunk USING GIN(metadata_json);
CREATE INDEX IF NOT EXISTS idx_legal_document_chunk_tsv ON rag.legal_document_chunk USING GIN(content_tsv);
CREATE INDEX IF NOT EXISTS idx_legal_document_chunk_embedding ON rag.legal_document_chunk USING ivfflat (embedding vector_l2_ops) WITH (lists = 16);

CREATE TABLE IF NOT EXISTS legal_agent.retrieval_evidence (
  id BIGSERIAL PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL REFERENCES legal_agent.legal_agent_run(run_id) ON DELETE CASCADE,
  evidence_id VARCHAR(64) NOT NULL,
  chunk_id VARCHAR(64),
  source_type VARCHAR(64) NOT NULL,
  authority_level VARCHAR(16) NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT,
  citation_anchor VARCHAR(256),
  quote TEXT NOT NULL,
  supported_claim TEXT,
  score NUMERIC(6,4),
  retrieval_method VARCHAR(64),
  metadata_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS legal_agent.approval_request (
  id BIGSERIAL PRIMARY KEY,
  approval_id VARCHAR(128) NOT NULL UNIQUE,
  run_id VARCHAR(64) NOT NULL REFERENCES legal_agent.legal_agent_run(run_id) ON DELETE CASCADE,
  agentledger_run_id VARCHAR(128) NOT NULL,
  agentledger_approval_id VARCHAR(128) NOT NULL,
  approval_key VARCHAR(256) NOT NULL UNIQUE,
  status VARCHAR(32) NOT NULL,
  risk_level VARCHAR(32) NOT NULL,
  reason TEXT,
  request_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  review_result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  document_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  requested_by VARCHAR(128),
  decided_by VARCHAR(128),
  decision_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_approval_request_run_status ON legal_agent.approval_request(run_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS legal_agent.generated_legal_document (
  id BIGSERIAL PRIMARY KEY,
  run_id VARCHAR(64) NOT NULL REFERENCES legal_agent.legal_agent_run(run_id) ON DELETE CASCADE,
  document_id VARCHAR(64) NOT NULL UNIQUE,
  document_type VARCHAR(64) NOT NULL,
  jurisdiction VARCHAR(32) NOT NULL,
  title TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  document_json JSONB NOT NULL,
  markdown TEXT,
  markdown_path TEXT,
  docx_path TEXT,
  facts_json JSONB NOT NULL,
  claims_json JSONB NOT NULL,
  legal_basis_json JSONB NOT NULL,
  evidence_list_json JSONB NOT NULL,
  amount_calculation_json JSONB NOT NULL,
  risk_notice_json JSONB NOT NULL,
  review_result_json JSONB NOT NULL,
  agentledger_artifact_id VARCHAR(128),
  agentledger_blob_ref TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at TIMESTAMPTZ
);

ALTER TABLE legal_agent.generated_legal_document
  ADD COLUMN IF NOT EXISTS docx_path TEXT;

CREATE INDEX IF NOT EXISTS idx_generated_legal_document_run ON legal_agent.generated_legal_document(run_id, created_at DESC);
