"""Canonical task ingest endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import time

from fastapi import APIRouter, HTTPException, Request, Response

from src.adapters.api_task_adapter import normalize_api_task_request
from src.contracts.task_contracts import TaskResult
from src.contracts.validation import validate_task_request
from src.services.job_state_store import JobStateStore
from src.services.metrics_registry import METRICS
from src.utils.structured_logger import get_structured_logger, log_event

router = APIRouter()
_store = JobStateStore()
_logger = get_structured_logger("cm3.tasks")


@router.post("/submit")
async def submit_task(payload: dict, request: Request, response: Response):
    """Validate and enqueue a canonical task request payload."""
    start = time.perf_counter()
    intent = payload.get("intent")
    body = payload.get("payload", {})

    trace_id_from_header = request.headers.get("x-trace-id")

    if not intent:
        METRICS.incr("tasks.failed")
        raise HTTPException(
            status_code=422,
            detail={"errors": [{"code": "CONTRACT_VALIDATION_ERROR", "message": "intent is required", "path": "intent"}]},
        )

    normalized = normalize_api_task_request(
        intent=intent,
        payload=body,
        task_id=payload.get("task_id"),
        trace_id=payload.get("trace_id") or trace_id_from_header,
        idempotency_key=payload.get("idempotency_key"),
        priority=payload.get("priority", "normal"),
        deadline=datetime.fromisoformat(payload["deadline"].replace("Z", "+00:00")) if payload.get("deadline") else datetime.now(timezone.utc),
    )

    _, errors = validate_task_request(normalized.model_dump())
    if errors:
        METRICS.incr("tasks.failed")
        raise HTTPException(status_code=422, detail={"errors": [e.model_dump() for e in errors]})

    if normalized.idempotency_key:
        existing = _store.get_by_idempotency_key(
            normalized.idempotency_key,
            intent=normalized.intent,
            source=normalized.source,
        )
        if existing:
            METRICS.incr("tasks.deduplicated")
            response.headers["x-trace-id"] = existing["trace_id"]
            log_event(
                _logger,
                "task deduplicated",
                trace_id=existing["trace_id"],
                task_id=existing["task_id"],
                intent=normalized.intent,
            )
            return {
                "task_id": existing["task_id"],
                "trace_id": existing["trace_id"],
                "status": existing["status"],
                "result": existing.get("result") or {"deduplicated": True},
                "errors": [],
                "warnings": ["duplicate idempotency key"],
                "version": "v1",
            }

    result = TaskResult(
        task_id=normalized.task_id,
        trace_id=normalized.trace_id,
        status="queued",
        result={"accepted": True},
    )
    _store.create(normalized, result)
    METRICS.incr("tasks.submitted")
    METRICS.observe_latency("tasks.submit", (time.perf_counter() - start) * 1000)
    response.headers["x-trace-id"] = normalized.trace_id
    log_event(
        _logger,
        "task submitted",
        trace_id=normalized.trace_id,
        task_id=normalized.task_id,
        intent=normalized.intent,
        source=normalized.source,
    )
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
