"""Submit-task MCP tool implementation (S22-4, #441).

Wires the ``submit_task`` MCP tool onto the existing Valdo canonical task-ingest
code path — the same normalise -> contract-validate -> idempotency-dedup ->
store-write sequence that the ``valdo submit-task`` CLI command
(:func:`src.commands.submit_task_command.run_submit_task_command`) and the
``POST /api/v1/tasks/submit`` REST endpoint
(:func:`src.api.routers.tasks.submit_task`) drive. Like the S22-1 / S22-2 / S22-3
tools (``parse_file`` / ``run_etl_pipeline`` / ``export_failed_rows``), this
module is a THIN adapter — no task-request shaping, contract validation, or
idempotency logic lives here. Normalisation stays in
:func:`src.adapters.api_task_adapter.normalize_api_task_request`, validation in
:func:`src.contracts.validation.validate_task_request`, and the dedup/persist in
:class:`src.services.job_state_store.JobStateStore`; the tool registration itself
stays in :mod:`src.mcp.server`.

The tool lets an agent submit a canonical task request over MCP and obtain the
queued task's id + status, exactly as a CLI or REST caller would. It is
idempotency-aware: when an ``idempotency_key`` is supplied and a task with that
key (for the same intent + source) already exists, the existing task id/status is
returned with a ``"duplicate idempotency key"`` warning instead of minting a
second task.

ToolError is reserved for caller-fixable problems: a missing/blank ``intent``, a
``payload`` that is not a JSON object, a malformed ``deadline``, or a request that
fails contract validation. The response carries no secrets — only the canonical
task id, trace id, status, the result/errors/warnings, and the contract version.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "submit_task_payload",
    "SUBMIT_TASK_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def submit_task_payload(
    intent: str,
    payload: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    priority: str = "normal",
    deadline: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a canonical task request over MCP and return its id + status.

    Thin adapter over the Valdo task-ingest path (the same code path the
    ``valdo submit-task`` CLI command and the ``POST /api/v1/tasks/submit`` REST
    endpoint drive). The raw arguments are normalised into a canonical
    :class:`~src.contracts.task_contracts.TaskRequest`, contract-validated,
    idempotency-deduplicated, and — on the happy path — persisted as a freshly
    ``queued`` task via :class:`~src.services.job_state_store.JobStateStore`. No
    request-shaping, validation, or persistence logic lives here.

    Idempotency: when *idempotency_key* is supplied and a task with that key (for
    the same intent + source) already exists in the store, the EXISTING task's
    id/status is returned (with a ``"duplicate idempotency key"`` warning) rather
    than minting a second task — exactly matching the CLI/REST dedup behaviour.

    Args:
        intent: Task intent (e.g. ``"validate"``, ``"compare"``). Required,
            non-blank.
        payload: Task payload object (e.g. ``{"source": "HR", "file": "..."}``).
            Defaults to an empty object. Must be a JSON object, not a scalar.
        task_id: Optional task id override; a UUID4 is minted when omitted.
        trace_id: Optional trace id override; a UUID4 is minted when omitted.
        idempotency_key: Optional dedup key; a repeat submission with the same
            key (for the same intent + source) returns the existing task.
        priority: Task priority — ``"low"`` | ``"normal"`` | ``"high"`` |
            ``"urgent"``. Defaults to ``"normal"``.
        deadline: Optional ISO-8601 deadline timestamp (``Z`` suffix accepted);
            defaults to now (UTC).

    Returns:
        The canonical task-result dict with:

        - ``task_id``: str — the queued (or deduplicated) task id.
        - ``trace_id``: str — the task's trace id.
        - ``status``: str — ``"queued"`` on a fresh submission, or the existing
          task's status on a dedup hit.
        - ``result``: dict — ``{"accepted": True}`` on a fresh submission.
        - ``errors``: list — always empty on success.
        - ``warnings``: list — ``["duplicate idempotency key"]`` on a dedup hit.
        - ``version``: str — the contract version (``"v1"``).

    Raises:
        ToolError: For caller-fixable problems: a missing/blank *intent*, a
            *payload* that is not a JSON object, a malformed *deadline*, or a
            request that fails contract validation.
    """
    if not intent or not isinstance(intent, str) or not intent.strip():
        raise ToolError("intent is required and must be a non-empty string")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ToolError("payload must be a JSON object")

    # Lazy imports keep the heavy ingest deps out of module import time and mirror
    # the CLI command's / API router's import sites.
    from src.adapters.api_task_adapter import normalize_api_task_request
    from src.contracts.task_contracts import TaskResult
    from src.contracts.validation import validate_task_request
    from src.services.job_state_store import JobStateStore

    if deadline:
        try:
            parsed_deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ToolError(f"Invalid deadline (expected ISO-8601): {exc}") from exc
    else:
        parsed_deadline = datetime.now(timezone.utc)

    req = normalize_api_task_request(
        intent=intent,
        payload=payload,
        task_id=task_id,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
        priority=priority,
        deadline=parsed_deadline,
    )

    _, errors = validate_task_request(req.model_dump())
    if errors:
        # Surface the first contract error message so the agent can self-correct;
        # contract failures are caller-fixable, never an infra fault.
        first = errors[0]
        detail = first.message
        if first.path:
            detail = f"{detail} (path: {first.path})"
        raise ToolError(f"Task request failed contract validation: {detail}")

    store = JobStateStore()
    if req.idempotency_key:
        existing = store.get_by_idempotency_key(
            req.idempotency_key, intent=req.intent, source=req.source
        )
        if existing:
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
        task_id=req.task_id,
        trace_id=req.trace_id,
        status="queued",
        result={"accepted": True},
    )
    store.create(req, result)
    logger.info("submit_task tool queued task_id=%s intent=%s", req.task_id, req.intent)
    return result.model_dump()


SUBMIT_TASK_DESCRIPTION = (
    "Submit a canonical Valdo task request over MCP and get back the queued "
    "task's id + status — the same task-ingest path the 'valdo submit-task' CLI "
    "command and the POST /api/v1/tasks/submit REST endpoint drive (normalise -> "
    "contract-validate -> idempotency-dedup -> store write). Accepts the task "
    "'intent' (required, e.g. 'validate' or 'compare'), an optional 'payload' "
    "object (the task's inputs), and optional 'task_id', 'trace_id', "
    "'idempotency_key', 'priority' (low|normal|high|urgent), and 'deadline' "
    "(ISO-8601) fields. Idempotency-aware: re-submitting with the same "
    "'idempotency_key' (for the same intent + source) returns the EXISTING task "
    "id/status with a 'duplicate idempotency key' warning instead of minting a "
    "second task. Returns the canonical task-result: 'task_id', 'trace_id', "
    "'status' ('queued' on a fresh submission), 'result', 'errors', 'warnings', "
    "and 'version'. The response carries no secrets. Raises a tool error only for "
    "caller-fixable problems: a missing/blank 'intent', a 'payload' that is not a "
    "JSON object, a malformed 'deadline', or a request that fails contract "
    "validation."
)
