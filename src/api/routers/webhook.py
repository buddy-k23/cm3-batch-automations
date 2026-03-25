"""Webhook validation endpoint for asynchronous file validation."""

import logging
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

from src.services.validate_service import run_validate_service

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory job registry (keyed by job_id).
_jobs: Dict[str, Dict[str, Any]] = {}


class WebhookValidateRequest(BaseModel):
    """Request body for the webhook validation endpoint.

    Attributes:
        file_path: Absolute path to the file to validate.
        mapping_id: Path to the mapping JSON configuration.
        rules_id: Optional path to the rules JSON configuration.
        thresholds: Optional path to the threshold configuration.
        callback_url: Optional URL to POST results when validation completes.
        metadata: Optional caller-supplied metadata returned in the callback.
    """

    file_path: str
    mapping_id: str
    rules_id: Optional[str] = None
    thresholds: Optional[str] = None
    callback_url: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class WebhookValidateResponse(BaseModel):
    """Immediate response returned when a validation job is accepted.

    Attributes:
        job_id: Unique identifier for the queued validation job.
        status: Current status of the job (always 'queued' on creation).
    """

    job_id: str
    status: str = "queued"


class JobStatusResponse(BaseModel):
    """Response for the job status polling endpoint.

    Attributes:
        job_id: Unique identifier for the validation job.
        status: Current status (queued, running, completed, failed).
        result: Validation result dict when completed, None otherwise.
        error: Error message if the job failed.
        metadata: Caller-supplied metadata from the original request.
    """

    job_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


async def _run_validation_job(
    job_id: str,
    file_path: str,
    mapping_id: str,
    rules_id: Optional[str],
    thresholds: Optional[str],
    callback_url: Optional[str],
    metadata: Optional[Dict[str, Any]],
) -> None:
    """Execute validation and optionally POST results to the callback URL.

    This function is designed to run as a FastAPI background task. It updates
    the in-memory ``_jobs`` registry with status transitions and results.

    Args:
        job_id: Unique job identifier.
        file_path: Path to the file to validate.
        mapping_id: Path to the mapping JSON.
        rules_id: Optional path to rules JSON.
        thresholds: Optional path to thresholds config.
        callback_url: Optional URL to POST the result to on completion.
        metadata: Optional caller-supplied metadata.
    """
    _jobs[job_id]["status"] = "running"

    try:
        result = run_validate_service(
            file=file_path,
            mapping=mapping_id,
            rules=rules_id,
        )
        _jobs[job_id]["status"] = "completed"
        _jobs[job_id]["result"] = result
    except Exception as exc:
        logger.exception("Webhook validation job %s failed", job_id)
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(exc)
        result = None

    # POST result to callback_url if provided.
    if callback_url:
        payload = {
            "job_id": job_id,
            "status": _jobs[job_id]["status"],
            "result": result,
            "metadata": metadata,
        }
        if _jobs[job_id].get("error"):
            payload["error"] = _jobs[job_id]["error"]

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(callback_url, json=payload)
                logger.info(
                    "Callback to %s returned status %d",
                    callback_url,
                    resp.status_code,
                )
        except Exception as cb_exc:
            logger.warning(
                "Failed to POST callback for job %s to %s: %s",
                job_id,
                callback_url,
                cb_exc,
            )


@router.post(
    "/validate",
    response_model=WebhookValidateResponse,
    status_code=202,
    summary="Submit async validation job",
)
async def submit_validation(
    request: WebhookValidateRequest,
    background_tasks: BackgroundTasks,
) -> WebhookValidateResponse:
    """Submit an asynchronous file validation job.

    The endpoint validates that the file exists, creates a job entry, and
    schedules the validation to run in the background. Returns immediately
    with a 202 Accepted and the ``job_id`` for status polling.

    Args:
        request: Webhook validation request body.
        background_tasks: FastAPI background task runner.

    Returns:
        WebhookValidateResponse with the new job_id.

    Raises:
        HTTPException: 400 if the file_path does not exist on disk.
    """
    fp = Path(request.file_path)
    if not fp.exists() or not fp.is_file():
        raise HTTPException(
            status_code=400,
            detail=f"File not found: {request.file_path}",
        )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "result": None,
        "error": None,
        "metadata": request.metadata,
    }

    background_tasks.add_task(
        _run_validation_job,
        job_id=job_id,
        file_path=request.file_path,
        mapping_id=request.mapping_id,
        rules_id=request.rules_id,
        thresholds=request.thresholds,
        callback_url=request.callback_url,
        metadata=request.metadata,
    )

    return WebhookValidateResponse(job_id=job_id, status="queued")


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Poll job status",
)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """Retrieve the current status and result of a validation job.

    Args:
        job_id: The UUID returned from the submit endpoint.

    Returns:
        JobStatusResponse with current status, result, and metadata.

    Raises:
        HTTPException: 404 if the job_id is not found.
    """
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    return JobStatusResponse(
        job_id=job["job_id"],
        status=job["status"],
        result=job.get("result"),
        error=job.get("error"),
        metadata=job.get("metadata"),
    )
