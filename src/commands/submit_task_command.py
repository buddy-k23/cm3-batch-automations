"""CLI command handler for ``valdo submit-task`` (S-followup, #422).

Relocated verbatim from ``src/main.py`` so that ``main.py`` is a thin CLI
registration layer (Architecture Principle #1/#6).  Normalises the CLI ingest
boundary into a canonical task request, validates it against the task
contracts, deduplicates on idempotency key, and persists the queued task via
the job state store.
"""

from __future__ import annotations

import json
from typing import Optional

import click


def run_submit_task_command(
    intent: str,
    payload: str,
    task_id: Optional[str],
    trace_id: Optional[str],
    idempotency_key: Optional[str],
    priority: str,
    deadline: Optional[str],
    machine_errors: bool,
) -> None:
    """Submit a canonical task request from the CLI ingest boundary.

    Parses the JSON ``payload``, normalises it into a canonical task request,
    validates it against the task contracts, performs idempotency-key
    deduplication, and persists a freshly-queued task on the happy path.

    Args:
        intent: Task intent (e.g. ``"validate"``, ``"compare"``).
        payload: JSON payload string.
        task_id: Optional task id override.
        trace_id: Optional trace id override.
        idempotency_key: Optional idempotency key for deduplication.
        priority: Task priority (e.g. ``"normal"``).
        deadline: Optional ISO-8601 deadline timestamp; defaults to now (UTC).
        machine_errors: When ``True``, emit machine-readable JSON error blocks.

    Raises:
        SystemExit: With code ``2`` on invalid JSON payload or when the request
            fails contract validation.
    """
    from datetime import datetime, timezone
    from src.adapters.cli_task_adapter import normalize_cli_task_request
    from src.contracts.validation import validate_task_request
    from src.contracts.task_contracts import TaskResult
    from src.services.job_state_store import JobStateStore

    try:
        payload_obj = json.loads(payload)
    except json.JSONDecodeError as exc:
        err = {"errors": [{"code": "INVALID_JSON", "message": str(exc), "path": "payload"}]}
        click.echo(json.dumps(err, indent=2) if machine_errors else f"Invalid payload JSON: {exc}")
        raise SystemExit(2)

    req = normalize_cli_task_request(
        intent=intent,
        payload=payload_obj,
        task_id=task_id,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
        priority=priority,
        deadline=datetime.fromisoformat(deadline.replace('Z', '+00:00')) if deadline else datetime.now(timezone.utc),
    )

    _, errors = validate_task_request(req.model_dump())
    if errors:
        err = {"errors": [e.model_dump() for e in errors]}
        click.echo(json.dumps(err, indent=2) if machine_errors else str(err))
        raise SystemExit(2)

    store = JobStateStore()
    if req.idempotency_key:
        existing = store.get_by_idempotency_key(req.idempotency_key, intent=req.intent, source=req.source)
        if existing:
            dedup_result = {
                "task_id": existing["task_id"],
                "trace_id": existing["trace_id"],
                "status": existing["status"],
                "result": existing.get("result") or {"deduplicated": True},
                "errors": [],
                "warnings": ["duplicate idempotency key"],
                "version": "v1",
            }
            click.echo(json.dumps(dedup_result, indent=2))
            return

    result = TaskResult(task_id=req.task_id, trace_id=req.trace_id, status='queued', result={"accepted": True})
    store.create(req, result)
    click.echo(json.dumps(result.model_dump(), indent=2))
