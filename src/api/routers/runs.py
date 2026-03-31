"""Run management API endpoints — trigger, status, schedule suite runs, and trend."""

import json
import uuid
import asyncio
from datetime import datetime
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Any, List, Optional

from src.api.auth import require_api_key
from src.services import trend_service, baseline_service
from src.services.deviation_detector import check_deviation

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

_RUN_HISTORY_PATH = Path("reports") / "run_history.json"


def load_run_history() -> list[dict[str, Any]]:
    """Load all run history entries from the JSON file on disk.

    Returns:
        List of run summary dicts from ``reports/run_history.json``.
        Returns an empty list if the file does not exist or contains
        invalid JSON.
    """
    if not _RUN_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_RUN_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

# Separate router for schedule endpoints mounted at /api/v1/schedules
schedule_router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])

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


@router.get("/trend")
async def get_run_trend(
    suite: Optional[str] = Query(None),
    days: int = Query(30),
    _: str = Depends(require_api_key),
) -> List[dict]:
    """Return daily-aggregated run history for charting.

    Args:
        suite: Filter to a specific suite (optional).
        days: Days to look back — must be 7, 14, 30, or 90.
        _: Injected auth context from ``require_api_key``.

    Returns:
        List of daily bucket dicts with date, total_runs, pass_runs,
        fail_runs, avg_quality_score, and pass_rate.

    Raises:
        HTTPException: 422 if ``days`` is not one of 7, 14, 30, 90.
    """
    try:
        return trend_service.get_trend(suite=suite, days=days)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


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


# ---------------------------------------------------------------------------
# Schedule endpoints  (GET /api/v1/schedules, POST /api/v1/schedules/run)
# ---------------------------------------------------------------------------


class ScheduleRunRequest(BaseModel):
    """Request body for POST /api/v1/schedules/run.

    Attributes:
        suite_name: Name of the suite to execute immediately.
        suites_dir: Optional override for the YAML suites directory.
    """

    suite_name: str
    suites_dir: Optional[str] = None


class ScheduleRunResponse(BaseModel):
    """Response body for POST /api/v1/schedules/run.

    Attributes:
        suite_name: Echoed from the request.
        run_id: UUID string generated for this execution.
        status: Execution result — ``"passed"``, ``"failed"``, or ``"error"``.
        message: Human-readable summary or error detail.
        step_results: List of per-step result dicts.
    """

    suite_name: str
    run_id: str
    status: str
    message: str
    step_results: List[Any]


@schedule_router.get("", response_model=List[dict])
async def list_schedules(suites_dir: Optional[str] = None):
    """List all configured validation suites.

    Reads YAML suite definitions from the suites directory and returns
    lightweight metadata for each suite.

    Args:
        suites_dir: Optional query-parameter override for the suites directory.

    Returns:
        List of dicts, each with ``name``, ``description``, and ``step_count``.
    """
    from src.services.scheduler_service import list_suites

    return list_suites(suites_dir=suites_dir)


@schedule_router.post("/run", response_model=ScheduleRunResponse, status_code=202)
async def run_schedule(request: ScheduleRunRequest):
    """Trigger a named validation suite run immediately.

    Executes the suite synchronously in the request thread and returns the
    full result once complete.

    Args:
        request: Suite name and optional suites directory override.

    Returns:
        ScheduleRunResponse with run_id, status, and per-step results.
    """
    from src.services.scheduler_service import run_suite_by_name

    result = run_suite_by_name(
        suite_name=request.suite_name,
        suites_dir=request.suites_dir,
    )
    return ScheduleRunResponse(
        suite_name=result["suite_name"],
        run_id=result.get("run_id", ""),
        status=result["status"],
        message=result.get("message", ""),
        step_results=result.get("step_results", []),
    )
