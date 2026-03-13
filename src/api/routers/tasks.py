"""Canonical task ingest endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from src.adapters.api_task_adapter import normalize_api_task_request
from src.contracts.task_contracts import TaskResult
from src.contracts.validation import validate_task_request
from src.services.job_state_store import JobStateStore

router = APIRouter()
_store = JobStateStore()


@router.post("/submit")
async def submit_task(payload: dict):
    """Validate and enqueue a canonical task request payload."""
    intent = payload.get("intent")
    body = payload.get("payload", {})
    if not intent:
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"code": "CONTRACT_VALIDATION_ERROR", "message": "intent is required", "path": "intent"}]},
        )

    normalized = normalize_api_task_request(
        intent=intent,
        payload=body,
        task_id=payload.get("task_id"),
        trace_id=payload.get("trace_id"),
        idempotency_key=payload.get("idempotency_key"),
        priority=payload.get("priority", "normal"),
        deadline=datetime.fromisoformat(payload["deadline"].replace("Z", "+00:00")) if payload.get("deadline") else datetime.now(timezone.utc),
    )

    _, errors = validate_task_request(normalized.model_dump())
    if errors:
        raise HTTPException(status_code=422, detail={"errors": [e.model_dump() for e in errors]})

    result = TaskResult(
        task_id=normalized.task_id,
        trace_id=normalized.trace_id,
        status="queued",
        result={"accepted": True},
    )
    _store.create(normalized, result)
    return result.model_dump()


@router.get("/{task_id}")
async def get_task(task_id: str):
    """Fetch task state from durable store."""
    row = _store.get(task_id)
    if not row:
        raise HTTPException(status_code=404, detail="task not found")
    return row


@router.get("")
async def list_tasks(limit: int = 50):
    """List recent task states from durable store."""
    return {"items": _store.list(limit=limit)}
