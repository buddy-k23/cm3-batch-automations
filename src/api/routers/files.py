"""File operation endpoints."""

import asyncio
import uuid
import sys
from pathlib import Path
import shutil

from fastapi import APIRouter, UploadFile, File, HTTPException, Body

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from src.api.models.file import (
    FileDetectionResult,
    FileParseRequest,
    FileParseResult,
    FileCompareRequest,
    FileCompareResult,
    FileValidateRequest,
    FileValidationResult,
    FileCompareAsyncCreateResponse,
    FileCompareAsyncStatusResponse,
)
from src.parsers.format_detector import FormatDetector
from src.services.compare_service import run_compare_service
from src.services.parse_service import run_parse_service
from src.services.validate_service import run_validate_service
from src.reports.renderers.comparison_renderer import HTMLReporter

router = APIRouter()

UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

MAPPINGS_DIR = Path("config/mappings")

# In-memory registry for async compare jobs.
_COMPARE_JOBS: dict[str, dict] = {}


@router.post("/detect", response_model=FileDetectionResult)
async def detect_format(file: UploadFile = File(...)):
    """Detect file format automatically.

    Analyzes the file and returns the detected format with confidence score.
    """
    upload_path = UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        detector = FormatDetector()
        result = detector.detect(str(upload_path))

        return FileDetectionResult(
            format=result['format'],
            confidence=result['confidence'],
            delimiter=result.get('delimiter'),
            line_count=result['line_count'],
            record_length=result.get('record_length'),
            sample_lines=result.get('sample_lines', [])[:5],
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error detecting format: {str(e)}")

    finally:
        if upload_path.exists():
            upload_path.unlink()


@router.post("/parse", response_model=FileParseResult)
async def parse_file(
    file: UploadFile = File(...),
    request: FileParseRequest = Body(...),
):
    """Parse file using specified mapping.

    Returns parsed data preview and download URL.
    """
    upload_path = UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        mapping_file = MAPPINGS_DIR / f"{request.mapping_id}.json"
        if not mapping_file.exists():
            raise HTTPException(status_code=404, detail=f"Mapping '{request.mapping_id}' not found")

        parsed = run_parse_service(
            file_path=str(upload_path),
            mapping_path=str(mapping_file),
            output_dir=str(UPLOADS_DIR),
        )

        output_file = Path(parsed["output_file"])
        return FileParseResult(
            rows_parsed=int(parsed["rows_parsed"]),
            columns=int(parsed["columns"]),
            preview=parsed.get("preview", []),
            download_url=f"/api/v1/files/download/{output_file.name}",
            errors=[],
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error parsing file: {str(e)}")

    finally:
        if upload_path.exists():
            upload_path.unlink()


@router.post("/validate", response_model=FileValidationResult)
async def validate_file(
    file: UploadFile = File(...),
    request: FileValidateRequest = Body(...),
):
    """Validate a file against a mapping with optional strict mode.

    Returns validation result including error list and optional HTML report URL.
    """
    upload_path = UPLOADS_DIR / f"validate_{file.filename}"
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        mapping_file = MAPPINGS_DIR / f"{request.mapping_id}.json"
        if not mapping_file.exists():
            raise HTTPException(status_code=404, detail=f"Mapping '{request.mapping_id}' not found")

        report_path = UPLOADS_DIR / f"validate_{upload_path.stem}.html" if request.output_html else None

        result = run_validate_service(
            file=str(upload_path),
            mapping=str(mapping_file),
            output=str(report_path) if report_path else None,
            detailed=request.detailed,
            strict_fixed_width=request.strict_fixed_width,
            strict_level=request.strict_level,
        )

        return FileValidationResult(
            valid=result.get("error_count", 0) == 0,
            total_rows=result.get("total_rows", 0),
            valid_rows=result.get("valid_rows", result.get("total_rows", 0)),
            invalid_rows=result.get("invalid_rows", result.get("error_count", 0)),
            errors=result.get("errors", []),
            warnings=result.get("warnings", []),
            quality_score=result.get("quality_score"),
            report_url=f"/uploads/{report_path.name}" if report_path and report_path.exists() else None,
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error validating file: {str(e)}")

    finally:
        if upload_path.exists():
            upload_path.unlink()


def _run_compare_with_mapping(
    upload_path1: Path,
    upload_path2: Path,
    request: FileCompareRequest,
) -> FileCompareResult:
    """Execute a synchronous file comparison and return the result model."""
    mapping_file = MAPPINGS_DIR / f"{request.mapping_id}.json"
    if not mapping_file.exists():
        raise HTTPException(status_code=404, detail=f"Mapping '{request.mapping_id}' not found")

    keys = ",".join(request.key_columns) if request.key_columns else None
    compare_result = run_compare_service(
        file1=str(upload_path1),
        file2=str(upload_path2),
        keys=keys,
        mapping=str(mapping_file),
        detailed=request.detailed,
        use_chunked=False,
    )

    report_path = UPLOADS_DIR / f"compare_{upload_path1.stem}_{upload_path2.stem}.html"
    HTMLReporter().generate(compare_result, str(report_path))

    return FileCompareResult(
        total_rows_file1=compare_result['total_rows_file1'],
        total_rows_file2=compare_result['total_rows_file2'],
        matching_rows=compare_result['matching_rows'],
        only_in_file1=compare_result.get('only_in_file1_count', len(compare_result.get('only_in_file1', []))),
        only_in_file2=compare_result.get('only_in_file2_count', len(compare_result.get('only_in_file2', []))),
        differences=compare_result.get('rows_with_differences', len(compare_result.get('differences', []))),
        report_url=f"/uploads/{report_path.name}",
        field_statistics=compare_result.get('field_statistics'),
    )


@router.post("/compare", response_model=FileCompareResult)
async def compare_files(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    request: FileCompareRequest = Body(...),
):
    """Compare two files and return a diff report.

    Returns comparison results and report URL.
    """
    upload_path1 = UPLOADS_DIR / f"file1_{file1.filename}"
    upload_path2 = UPLOADS_DIR / f"file2_{file2.filename}"

    with open(upload_path1, "wb") as buffer:
        shutil.copyfileobj(file1.file, buffer)
    with open(upload_path2, "wb") as buffer:
        shutil.copyfileobj(file2.file, buffer)

    try:
        return _run_compare_with_mapping(upload_path1, upload_path2, request)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error comparing files: {str(e)}")

    finally:
        for path in [upload_path1, upload_path2]:
            if path.exists():
                path.unlink()


async def _run_compare_job(
    job_id: str,
    file1_path: Path,
    file2_path: Path,
    request: FileCompareRequest,
) -> None:
    """Background coroutine: run blocking compare in thread pool, update job registry."""
    try:
        result = await asyncio.to_thread(_run_compare_with_mapping, file1_path, file2_path, request)
        _COMPARE_JOBS[job_id]["status"] = "completed"
        _COMPARE_JOBS[job_id]["result"] = result.model_dump()
    except Exception as e:
        _COMPARE_JOBS[job_id]["status"] = "failed"
        _COMPARE_JOBS[job_id]["error"] = str(e)
    finally:
        for p in [file1_path, file2_path]:
            if p.exists():
                p.unlink()


@router.post("/compare-async", response_model=FileCompareAsyncCreateResponse)
async def compare_files_async(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    request: FileCompareRequest = Body(...),
):
    """Create an async compare job and return the job ID.

    Poll ``GET /compare-jobs/{job_id}`` for status and result.
    """
    job_id = str(uuid.uuid4())
    upload_path1 = UPLOADS_DIR / f"job_{job_id}_file1_{file1.filename}"
    upload_path2 = UPLOADS_DIR / f"job_{job_id}_file2_{file2.filename}"

    with open(upload_path1, "wb") as buffer:
        shutil.copyfileobj(file1.file, buffer)
    with open(upload_path2, "wb") as buffer:
        shutil.copyfileobj(file2.file, buffer)

    _COMPARE_JOBS[job_id] = {"status": "queued", "result": None, "error": None}
    task = asyncio.create_task(_run_compare_job(job_id, upload_path1, upload_path2, request))
    _COMPARE_JOBS[job_id]["status"] = "running"
    _COMPARE_JOBS[job_id]["task"] = task

    return FileCompareAsyncCreateResponse(job_id=job_id, status="running")


@router.get("/compare-jobs/{job_id}", response_model=FileCompareAsyncStatusResponse)
async def compare_job_status(job_id: str):
    """Fetch the status and result of an async compare job."""
    job = _COMPARE_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Compare job '{job_id}' not found")

    result = FileCompareResult(**job["result"]) if job.get("result") else None
    return FileCompareAsyncStatusResponse(
        job_id=job_id,
        status=job.get("status", "unknown"),
        result=result,
        error=job.get("error"),
    )
