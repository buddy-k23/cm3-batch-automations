"""Run management API endpoints — trigger and status for test suite runs."""

import uuid
import asyncio
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

# In-memory run status store (replace with file/DB in production)
_run_store: dict[str, dict] = {}


class TriggerRequest(BaseModel):
    """Request body for POST /api/v1/runs/trigger.

    Attributes:
        suite: Path to the test suite YAML file to run.
        params: Optional dict of substitution parameters (e.g. run_date).
        env: Environment name passed to the suite runner. Defaults to "dev".
        output_dir: Directory for HTML reports. Defaults to "reports".
    """

    suite: str
    params: Optional[dict] = None
    env: Optional[str] = "dev"
    output_dir: Optional[str] = "reports"


class TriggerResponse(BaseModel):
    """Response body for POST /api/v1/runs/trigger.

    Attributes:
        run_id: Short unique identifier for this queued run.
        status: Initial status — always "queued" on success.
        message: Human-readable confirmation including the run_id.
    """

    run_id: str
    status: str
    message: str


@router.post("/trigger", response_model=TriggerResponse, status_code=202)
async def trigger_run(request: TriggerRequest):
    """Trigger a test suite run asynchronously.

    Queues the specified suite for background execution and returns immediately
    with a ``run_id`` that can be used to poll :func:`get_run_status`.

    Args:
        request: Suite path, optional params dict, env, and output dir.

    Returns:
        TriggerResponse with run_id to poll for status.
    """
    run_id = str(uuid.uuid4())[:8]
    _run_store[run_id] = {"status": "queued", "started_at": datetime.utcnow().isoformat()}

    async def _run():
        _run_store[run_id]["status"] = "running"
        try:
            from src.commands.run_tests_command import run_suite_from_path
            await asyncio.to_thread(
                run_suite_from_path,
                request.suite,
                params=request.params or {},
                env=request.env,
                output_dir=request.output_dir,
            )
            _run_store[run_id]["status"] = "completed"
        except Exception as e:
            _run_store[run_id]["status"] = "error"
            _run_store[run_id]["error"] = str(e)

    asyncio.create_task(_run())
    return TriggerResponse(run_id=run_id, status="queued", message=f"Suite run queued as {run_id}")


@router.get("/{run_id}")
async def get_run_status(run_id: str):
    """Get status of a triggered run.

    Args:
        run_id: The run ID returned by POST /trigger.

    Returns:
        Dict with status, started_at, and optional error fields.

    Raises:
        HTTPException: 404 if run_id not found.
    """
    if run_id not in _run_store:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return _run_store[run_id]
