from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .enums import NodeName, NodeStatus, RunStatus


class OutputOptions(BaseModel):
    document_type: str = "labor_arbitration_application"
    format: str = "markdown"
    require_human_review: bool = True
    approval_timeout_seconds: int | None = Field(default=None, ge=1)


class CreateRunInput(BaseModel):
    text: str
    file_ids: list[str] = Field(default_factory=list)


class CreateRunRequest(BaseModel):
    task_type: str = "document_generation"
    legal_domain: str = "labor_dispute"
    jurisdiction: str = "CN-BJ"
    user_input_timeout_seconds: int | None = Field(default=None, ge=1)
    output_options: OutputOptions = Field(default_factory=OutputOptions)
    input: CreateRunInput


class CaseSearchRequest(BaseModel):
    domain: str = "labor_dispute"
    claims: list[str] = Field(default_factory=list)
    region: str = "CN"
    top_k: int = Field(default=10, ge=1, le=50)
    query: str | None = None


class CreateRunResponse(BaseModel):
    request_id: str
    run_id: str
    agentledger_run_id: str
    run_status: RunStatus
    current_node: NodeName
    missing_fields: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    question_groups: list[dict[str, Any]] = Field(default_factory=list)


class UploadFileResponse(BaseModel):
    request_id: str
    file_id: str
    original_filename: str
    content_type: str | None = None
    size_bytes: int
    sha256: str
    parse_status: str
    chunk_count: int
    text_preview: str | None = None


class RunStatusResponse(BaseModel):
    request_id: str
    run_id: str
    run_status: RunStatus
    current_node: NodeName | None = None
    current_node_status: NodeStatus | None = None
    progress: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    question_groups: list[dict[str, Any]] = Field(default_factory=list)
    requires_user_input: bool = False
    requires_approval: bool = False
    last_error: str | None = None
    updated_at: datetime | None = None


class AddFactsRequest(BaseModel):
    facts: dict[str, Any]
    confirmed_missing_fields: list[dict[str, Any]] = Field(default_factory=list)


class AddFactsResponse(BaseModel):
    request_id: str
    run_id: str
    run_status: RunStatus
    current_node: NodeName | None = None
    next_actions: list[dict[str, Any]] = Field(default_factory=list)


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    approver: str = "demo-reviewer"
    reason: str = ""


class ApprovalDecisionResponse(BaseModel):
    request_id: str
    run_id: str
    approval_id: str
    run_status: RunStatus
    current_node: NodeName | None = None
    approval_status: str


class CancelRunRequest(BaseModel):
    reason: str = "user_cancelled"
    requested_by: str = "demo-user"


class CancelRunResponse(BaseModel):
    request_id: str
    run_id: str
    run_status: RunStatus
    current_node: NodeName | None = None
    cancellation_status: str
    reason: str
