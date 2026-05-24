from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from legal_agent.db.repository import RunRepository


@dataclass(frozen=True)
class IdempotencyGuard:
    scope: str
    key: str
    request_hash: str
    replay_response: dict[str, Any] | None = None


def request_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def require_idempotency_key(raw_key: str | None) -> str:
    key = (raw_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required for this write endpoint")
    if len(key) > 200:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is too long")
    return key


def begin_idempotent_request(repo: RunRepository, *, scope: str, raw_key: str | None, body: Any) -> IdempotencyGuard:
    key = require_idempotency_key(raw_key)
    digest = request_hash(body)
    decision = repo.begin_idempotent_request(scope=scope, idempotency_key=key, request_hash=digest)
    status = decision["status"]
    if status == "started":
        return IdempotencyGuard(scope=scope, key=key, request_hash=digest)
    if status == "replay":
        return IdempotencyGuard(scope=scope, key=key, request_hash=digest, replay_response=dict(decision["response_json"]))
    if status == "conflict":
        raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different request")
    raise HTTPException(status_code=409, detail="Idempotency-Key request is still in progress")


def complete_idempotent_request(repo: RunRepository, guard: IdempotencyGuard, response_json: dict[str, Any]) -> dict[str, Any]:
    repo.complete_idempotent_request(
        scope=guard.scope,
        idempotency_key=guard.key,
        request_hash=guard.request_hash,
        response_json=response_json,
    )
    return response_json
