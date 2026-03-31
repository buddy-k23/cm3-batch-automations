"""File operation endpoints."""

import asyncio
import uuid
import sys
import time
from pathlib import Path
import shutil

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Body, Depends
from fastapi.responses import FileResponse

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
    DbCompareResult,
)
from src.parsers.format_detector import FormatDetector
from src.services.compare_service import run_compare_service
from src.services.db_file_compare_service import compare_db_to_file
from src.services.parse_service import run_parse_service
from src.services.validate_service import run_validate_service
from src.services.multi_record_validate_service import run_multi_record_validate_service
from src.reports.renderers.comparison_renderer import HTMLReporter
from src.services.compare_job_store import CompareJobStore
from src.services.retry_policy import execute_with_retries
from src.services.metrics_registry import METRICS
from src.utils.structured_logger import get_structured_logger, log_event
from src.validators.threshold import ThresholdEvaluator
from src.services.drift_detector import detect_drift
from src.api.auth import require_api_key
from src.services.error_extractor import extract_error_rows

router = APIRouter()

_CHUNK_THRESHOLD_BYTES: int = 50 * 1024 * 1024  # 50 MB


def _should_use_chunked(path: Path) -> bool:
    """Return True when the file at *path* meets the chunked-processing threshold.

    Args:
        path: Filesystem path to the uploaded file.

    Returns:
        True if the file size is >= _CHUNK_THRESHOLD_BYTES, False otherwise
        (including when the file does not exist).
    """
    try:
        return path.stat().st_size >= _CHUNK_THRESHOLD_BYTES
    except OSError:
        return False


UPLOADS_DIR = Path("uploads")
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

MAPPINGS_DIR = Path("config/mappings")

# Durable registry for async compare jobs.
_COMPARE_JOB_STORE = CompareJobStore()
_LOGGER = get_structured_logger("cm3.files")


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
    mapping_id: str = Form(...),
    output_format: str = Form("csv"),
):
    """Parse file using specified mapping.

    Returns parsed data preview and download URL.
    """
    upload_path = UPLOADS_DIR / file.filename
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"
        if not mapping_file.exists():
            raise HTTPException(status_code=404, detail=f"Mapping '{mapping_id}' not found")

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
    mapping_id: str = Form(None),
    detailed: bool = Form(True),
    strict_fixed_width: bool = Form(False),
    strict_level: str = Form("format"),
    output_html: bool = Form(True),
    suppress_pii: bool = Form(True),
    multi_record_config: UploadFile = File(None),
):
    """Validate a file against a mapping or multi-record YAML config.

    Returns validation result including error list and optional HTML report URL.
    Either ``mapping_id`` or ``multi_record_config`` must be provided.  When
    both are supplied ``multi_record_config`` takes precedence.

    Args:
        file: The batch data file to validate.
        mapping_id: Mapping config identifier (JSON filename stem under
            config/mappings/).  Required unless ``multi_record_config`` is
            provided.
        detailed: Include detailed field-level analysis.
        strict_fixed_width: Enable strict fixed-width position checks.
        strict_level: Validation strictness level (``'format'`` or ``'all'``).
        output_html: When True, generate an HTML report alongside JSON results.
        suppress_pii: When True (default), redact raw field values from the
            HTML report. Set to False to show actual values in the report.
        multi_record_config: Optional YAML file describing a multi-record
            config.  When present, ``mapping_id`` is not required and
            multi-record validation is performed instead of field-level
            validation.
    """
    if multi_record_config is None and not mapping_id:
        raise HTTPException(
            status_code=422,
            detail="Either 'mapping_id' or 'multi_record_config' must be provided",
        )

    upload_path = UPLOADS_DIR / f"validate_{file.filename}"
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # --- Multi-record path ---
        if multi_record_config is not None:
            config_bytes = await multi_record_config.read()
            config_yaml = config_bytes.decode("utf-8", errors="replace")
            try:
                result = run_multi_record_validate_service(
                    file_path=str(upload_path),
                    config_yaml=config_yaml,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

            cross_violations = result.get("cross_type_violations", [])
            errors = [
                {"message": v.get("message", ""), "severity": v.get("severity", "error")}
                for v in cross_violations
                if v.get("severity") == "error"
            ]
            warnings = [
                {"message": v.get("message", ""), "severity": v.get("severity", "warning")}
                for v in cross_violations
                if v.get("severity") == "warning"
            ]
            return FileValidationResult(
                valid=result.get("valid", False),
                total_rows=result.get("total_rows", 0),
                valid_rows=result.get("total_rows", 0) if result.get("valid") else 0,
                invalid_rows=len(errors),
                errors=errors,
                warnings=warnings,
                quality_score=None,
                report_url=None,
            )

        # --- Standard field-level validation path ---
        mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"
        if not mapping_file.exists():
            raise HTTPException(status_code=404, detail=f"Mapping '{mapping_id}' not found")

        report_path = UPLOADS_DIR / f"validate_{upload_path.stem}.html" if output_html else None

        result = run_validate_service(
            file=str(upload_path),
            mapping=str(mapping_file),
            output=str(report_path) if report_path else None,
            detailed=detailed,
            strict_fixed_width=strict_fixed_width,
            strict_level=strict_level,
            use_chunked=_should_use_chunked(upload_path),
            suppress_pii=suppress_pii,
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


def _build_threshold_result(compare_result: dict) -> dict:
    """Evaluate comparison results against default thresholds.

    Adapts the raw service output to the format expected by
    :class:`~src.validators.threshold.ThresholdEvaluator`, normalising the
    ``only_in_file1`` and ``only_in_file2`` values so that
    :meth:`~src.validators.threshold.ThresholdEvaluator.evaluate` can always
    call ``len()`` on them.

    Three shapes are handled:

    - **Non-chunked**: values are ``pd.DataFrame`` objects — ``len()`` works
      natively, no adaptation needed.
    - **Chunked**: values may be empty lists (``[]``) with a separate
      ``only_in_file1_count`` integer field — we substitute a synthetic list
      of the correct length.
    - **Structure-incompatible early-exit**: values are already ``0`` integers
      — substituted with ``[]``.

    Args:
        compare_result: Raw dict returned by :func:`run_compare_service`.

    Returns:
        Dict with keys ``"passed"`` (bool) and ``"overall_result"`` (str)
        at minimum, plus ``"metrics"`` entries.
    """
    adapted = dict(compare_result)
    for field, count_key in (
        ("only_in_file1", "only_in_file1_count"),
        ("only_in_file2", "only_in_file2_count"),
    ):
        value = adapted.get(field)
        # DataFrames and lists support len() already — skip adaptation.
        try:
            len(value)  # type: ignore[arg-type]
        except TypeError:
            # Value is an integer (chunked count or 0 from early-exit).
            count = adapted.get(count_key, value or 0)
            adapted[field] = [None] * int(count)

    evaluator = ThresholdEvaluator()
    evaluation = evaluator.evaluate(adapted)
    return {
        "passed": evaluation["passed"],
        "overall_result": evaluation["overall_result"].value,
        "metrics": evaluation.get("metrics", {}),
    }


def _run_compare_with_mapping(
    upload_path1: Path,
    upload_path2: Path,
    request: FileCompareRequest,
) -> FileCompareResult:
    """Execute a synchronous file comparison and return the result model.

    Resolves the mapping file from ``request.mapping_id``, delegates to
    :func:`run_compare_service`, generates a report (HTML or JSON) based on
    ``request.output_format``, evaluates default thresholds, and wraps the
    raw service output into a :class:`FileCompareResult` response model.

    Chunked processing is enabled automatically when ``request.key_columns``
    is non-empty **and** at least one of the two files meets the
    ``_CHUNK_THRESHOLD_BYTES`` size threshold.

    Args:
        upload_path1: Filesystem path to the first uploaded file.
        upload_path2: Filesystem path to the second uploaded file.
        request: Parsed compare request containing ``mapping_id``,
            optional ``key_columns``, ``detailed`` flag, ``output_format``
            (``"html"`` or ``"json"``), and ``chunk_size``.

    Returns:
        A :class:`FileCompareResult` containing row counts, match/difference
        counts, an optional ``field_statistics`` mapping, a
        ``threshold_result`` dict, and either a ``report_url`` (HTML) or
        ``download_url`` (JSON) pointing to the generated report file.

    Raises:
        HTTPException: 404 if the mapping file for ``request.mapping_id``
            does not exist.
    """
    mapping_file = MAPPINGS_DIR / f"{request.mapping_id}.json"
    if not mapping_file.exists():
        raise HTTPException(status_code=404, detail=f"Mapping '{request.mapping_id}' not found")

    keys = ",".join(request.key_columns) if request.key_columns else None
    # Enable chunked compare only when key_columns are present (ChunkedFileComparator
    # requires keys) and at least one file is large enough to warrant it.
    use_chunked = bool(keys) and (
        _should_use_chunked(upload_path1) or _should_use_chunked(upload_path2)
    )
    compare_result = run_compare_service(
        file1=str(upload_path1),
        file2=str(upload_path2),
        keys=keys,
        mapping=str(mapping_file),
        detailed=request.detailed,
        chunk_size=request.chunk_size,
        use_chunked=use_chunked,
    )

    report_stem = f"compare_{upload_path1.stem}_{upload_path2.stem}"
    report_url: str | None = None
    download_url: str | None = None

    if request.output_format == "json":
        import json as _json
        report_path = UPLOADS_DIR / f"{report_stem}.json"
        with open(report_path, "w", encoding="utf-8") as fh:
            _json.dump(compare_result, fh, indent=2, default=str)
        download_url = f"/uploads/{report_path.name}"
    else:
        report_path = UPLOADS_DIR / f"{report_stem}.html"
        HTMLReporter().generate(compare_result, str(report_path))
        report_url = f"/uploads/{report_path.name}"

    threshold_result = _build_threshold_result(compare_result)

    return FileCompareResult(
        total_rows_file1=compare_result['total_rows_file1'],
        total_rows_file2=compare_result['total_rows_file2'],
        matching_rows=compare_result['matching_rows'],
        only_in_file1=compare_result.get('only_in_file1_count', len(compare_result.get('only_in_file1', []))),
        only_in_file2=compare_result.get('only_in_file2_count', len(compare_result.get('only_in_file2', []))),
        differences=compare_result.get('rows_with_differences', len(compare_result.get('differences', []))),
        report_url=report_url,
        download_url=download_url,
        field_statistics=compare_result.get('field_statistics'),
        threshold_result=threshold_result,
    )


@router.post("/compare", response_model=FileCompareResult)
async def compare_files(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    mapping_id: str = Form(...),
    key_columns: str = Form(
        "",
        description="Comma-separated key column names used for row matching. "
        "When empty, row-by-row positional comparison is used.",
    ),
    detailed: bool = Form(True, description="Include field-level diff analysis."),
    output_format: str = Form(
        "html",
        description="Report output format: ``html`` (default) or ``json``. "
        "HTML produces a ``report_url``; JSON produces a ``download_url``.",
    ),
    chunk_size: int = Form(
        100_000,
        description="Row chunk size for large-file chunked processing. "
        "Matches the CLI ``--chunk-size`` default of 100 000.",
    ),
):
    """Compare two files and return a diff report.

    Accepts the same options as the CLI ``compare`` command, including
    ``output_format`` (html/json), ``chunk_size``, and ``key_columns``.
    The response always includes a ``threshold_result`` with pass/fail
    evaluated against default thresholds.

    Returns comparison results and either a ``report_url`` (HTML) or
    ``download_url`` (JSON) pointing to the generated report.
    """
    upload_path1 = UPLOADS_DIR / f"file1_{file1.filename}"
    upload_path2 = UPLOADS_DIR / f"file2_{file2.filename}"

    with open(upload_path1, "wb") as buffer:
        shutil.copyfileobj(file1.file, buffer)
    with open(upload_path2, "wb") as buffer:
        shutil.copyfileobj(file2.file, buffer)

    keys_list = [k.strip() for k in key_columns.split(",") if k.strip()]
    request = FileCompareRequest(
        mapping_id=mapping_id,
        key_columns=keys_list,
        detailed=detailed,
        output_format=output_format,
        chunk_size=chunk_size,
    )

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
    """Background coroutine with retry/backoff and dead-letter on final failure."""
    started = time.perf_counter()
    try:
        result = await asyncio.to_thread(
            lambda: execute_with_retries(
                lambda: _run_compare_with_mapping(file1_path, file2_path, request),
                max_attempts=3,
                base_delay_seconds=0.25,
            )
        )
        _COMPARE_JOB_STORE.update(job_id, status="completed", result=result.model_dump())
        METRICS.incr("compare.async.success")
        log_event(_LOGGER, "compare async job completed", trace_id=job_id, job_id=job_id, status="completed")
    except Exception as e:
        _COMPARE_JOB_STORE.update(job_id, status="dead-letter", error=str(e))
        METRICS.incr("compare.async.dead_letter")
        log_event(_LOGGER, "compare async job dead-letter", trace_id=job_id, job_id=job_id, status="dead-letter", error=str(e))
    finally:
        METRICS.observe_latency("compare.async", (time.perf_counter() - started) * 1000)
        for p in [file1_path, file2_path]:
            if p.exists():
                p.unlink()


@router.post("/compare-async", response_model=FileCompareAsyncCreateResponse)
async def compare_files_async(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    mapping_id: str = Form(...),
    key_columns: str = Form(
        "",
        description="Comma-separated key column names used for row matching. "
        "When empty, row-by-row positional comparison is used.",
    ),
    detailed: bool = Form(True, description="Include field-level diff analysis."),
    output_format: str = Form(
        "html",
        description="Report output format: ``html`` (default) or ``json``.",
    ),
    chunk_size: int = Form(
        100_000,
        description="Row chunk size for large-file chunked processing.",
    ),
):
    """Create an async compare job and return the job ID.

    Accepts the same parameters as ``POST /compare`` including
    ``output_format`` and ``chunk_size`` for parity with the CLI.
    Poll ``GET /compare-jobs/{job_id}`` for status and result.
    """
    job_id = str(uuid.uuid4())
    upload_path1 = UPLOADS_DIR / f"job_{job_id}_file1_{file1.filename}"
    upload_path2 = UPLOADS_DIR / f"job_{job_id}_file2_{file2.filename}"

    with open(upload_path1, "wb") as buffer:
        shutil.copyfileobj(file1.file, buffer)
    with open(upload_path2, "wb") as buffer:
        shutil.copyfileobj(file2.file, buffer)

    keys_list = [k.strip() for k in key_columns.split(",") if k.strip()]
    request = FileCompareRequest(
        mapping_id=mapping_id,
        key_columns=keys_list,
        detailed=detailed,
        output_format=output_format,
        chunk_size=chunk_size,
    )

    _COMPARE_JOB_STORE.create(job_id, status="queued")
    _COMPARE_JOB_STORE.update(job_id, status="running")
    METRICS.incr("compare.async.submitted")
    log_event(_LOGGER, "compare async job submitted", trace_id=job_id, job_id=job_id, status="running")
    asyncio.create_task(_run_compare_job(job_id, upload_path1, upload_path2, request))

    return FileCompareAsyncCreateResponse(job_id=job_id, status="running")


@router.get("/compare-jobs/{job_id}", response_model=FileCompareAsyncStatusResponse)
async def compare_job_status(job_id: str):
    """Fetch the status and result of an async compare job."""
    job = _COMPARE_JOB_STORE.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Compare job '{job_id}' not found")

    result = FileCompareResult(**job["result"]) if job.get("result") else None
    return FileCompareAsyncStatusResponse(
        job_id=job_id,
        status=job.get("status", "unknown"),
        result=result,
        error=job.get("error"),
    )


@router.post("/db-compare", response_model=DbCompareResult)
async def db_compare(
    actual_file: UploadFile = File(...),
    query_or_table: str = Form(...),
    mapping_id: str = Form(...),
    key_columns: str = Form(""),
    output_format: str = Form("json"),
):
    """Extract data from Oracle and compare against an uploaded actual batch file.

    Runs the full DB extract → temp file → compare pipeline and returns a
    unified result containing workflow metadata and comparison statistics.

    Args:
        actual_file: The actual batch file to compare against.
        query_or_table: SQL SELECT statement or bare Oracle table name.
        mapping_id: ID of the JSON mapping config (must exist in MAPPINGS_DIR).
        key_columns: Comma-separated key column names for row matching.
        output_format: Desired output format (``"json"`` or ``"html"``).

    Returns:
        DbCompareResult with workflow status, row counts, and diff statistics.

    Raises:
        HTTPException: 404 if the mapping is not found.
        HTTPException: 500 if DB extraction or comparison fails.
    """
    mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"
    if not mapping_file.exists():
        raise HTTPException(status_code=404, detail=f"Mapping '{mapping_id}' not found")

    import json as _json
    mapping_config = _json.loads(mapping_file.read_text(encoding="utf-8"))

    upload_path = UPLOADS_DIR / f"dbcompare_{actual_file.filename}"
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(actual_file.file, buffer)

    try:
        key_columns_list = [k.strip() for k in key_columns.split(",") if k.strip()]

        result = compare_db_to_file(
            query_or_table=query_or_table,
            mapping_config=mapping_config,
            actual_file=str(upload_path),
            output_format=output_format,
            key_columns=key_columns_list or None,
        )

        workflow = result.get("workflow", {})
        compare = result.get("compare", {})

        rows_with_diffs = compare.get(
            "rows_with_differences", compare.get("differences", 0)
        )

        return DbCompareResult(
            workflow_status=workflow.get("status", "unknown"),
            db_rows_extracted=workflow.get("db_rows_extracted", 0),
            query_or_table=workflow.get("query_or_table", query_or_table),
            total_rows_file1=compare.get("total_rows_file1", 0),
            total_rows_file2=compare.get("total_rows_file2", 0),
            matching_rows=compare.get("matching_rows", 0),
            only_in_file1=compare.get("only_in_file1", 0),
            only_in_file2=compare.get("only_in_file2", 0),
            differences=rows_with_diffs,
            structure_compatible=compare.get("structure_compatible"),
            structure_errors=compare.get("structure_errors"),
            field_statistics=compare.get("field_statistics"),
        )

    except HTTPException:
        raise
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"DB extraction failed: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error running db-compare: {exc}")

    finally:
        if upload_path.exists():
            upload_path.unlink()


@router.post("/export-errors")
async def export_errors(
    file: UploadFile = File(...),
    mapping_id: str = Form(None),
    multi_record_config: UploadFile = File(None),
    _: str = Depends(require_api_key),
):
    """Run validation and return a file containing only the failed rows.

    Validates the uploaded file against the provided mapping or multi-record
    YAML config, then extracts all rows that produced validation errors into a
    downloadable text file.  The response is always 200; when there are no
    errors the returned file is effectively empty (fixed-width) or header-only
    (delimited).

    Args:
        file: The batch data file to validate.
        mapping_id: Mapping config identifier (JSON filename stem under
            ``config/mappings/``).  Required unless ``multi_record_config``
            is provided.
        multi_record_config: Optional YAML file describing a multi-record
            config.  When present, ``mapping_id`` is not required.

    Returns:
        A ``FileResponse`` with ``Content-Disposition: attachment`` and
        filename ``errors_<original_filename>``.

    Raises:
        HTTPException: 422 if neither ``mapping_id`` nor
            ``multi_record_config`` is supplied.
        HTTPException: 404 if the ``mapping_id`` mapping file does not exist.
        HTTPException: 500 on unexpected service errors.
    """
    if multi_record_config is None and not mapping_id:
        raise HTTPException(
            status_code=422,
            detail="Either 'mapping_id' or 'multi_record_config' must be provided",
        )

    upload_path = UPLOADS_DIR / f"export_errors_{file.filename}"
    error_output_path = UPLOADS_DIR / f"errors_{file.filename}"

    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # --- Multi-record path ---
        if multi_record_config is not None:
            config_bytes = await multi_record_config.read()
            config_yaml = config_bytes.decode("utf-8", errors="replace")
            try:
                result = run_multi_record_validate_service(
                    file_path=str(upload_path),
                    config_yaml=config_yaml,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

            # Convert cross-type violations to error format compatible with extractor.
            cross_violations = result.get("cross_type_violations", [])
            errors = [
                {"row": v.get("row", 0), "message": v.get("message", "")}
                for v in cross_violations
                if v.get("severity") == "error"
            ]
            validation_result_dict = {"errors": errors}
        else:
            # --- Standard field-level validation path ---
            mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"
            if not mapping_file.exists():
                raise HTTPException(
                    status_code=404,
                    detail=f"Mapping '{mapping_id}' not found",
                )

            validation_result_dict = run_validate_service(
                file=str(upload_path),
                mapping=str(mapping_file),
                use_chunked=_should_use_chunked(upload_path),
            )

        extract_error_rows(
            file_path=str(upload_path),
            validation_result=validation_result_dict,
            output_path=str(error_output_path),
        )

        return FileResponse(
            path=str(error_output_path),
            filename=f"errors_{file.filename}",
            media_type="text/plain",
        )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error exporting errors: {exc}")

    finally:
        if upload_path.exists():
            upload_path.unlink()


@router.post("/detect-drift")
async def detect_drift_endpoint(
    file: UploadFile = File(...),
    mapping_id: str = Form(...),
    _key=Depends(require_api_key),
):
    """Detect schema drift between an uploaded file and a mapping config.

    Saves the uploaded file to the uploads directory, loads the named mapping
    from ``config/mappings/{mapping_id}.json``, and runs the drift detector.

    Args:
        file: The batch data file to inspect.
        mapping_id: Stem of the mapping JSON file (no ``.json`` extension).
        _key: Injected API key auth context (from ``require_api_key``).

    Returns:
        Drift report dict with keys ``drifted`` (bool) and ``fields`` (list).
        Each entry in ``fields`` contains ``name``, ``expected_start``,
        ``actual_start``, ``expected_length``, ``actual_length``, and
        ``severity`` (``'warning'`` or ``'error'``).

    Raises:
        HTTPException: 404 when ``mapping_id`` does not resolve to a file.
        HTTPException: 422 when the ``file`` field is missing (FastAPI automatic).
        HTTPException: 500 on unexpected errors.
    """
    import json as _json

    mapping_file = MAPPINGS_DIR / f"{mapping_id}.json"
    if not mapping_file.exists():
        raise HTTPException(status_code=404, detail=f"Mapping '{mapping_id}' not found")

    upload_path = UPLOADS_DIR / f"drift_{file.filename}"
    with open(upload_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        mapping = _json.loads(mapping_file.read_text(encoding="utf-8"))
        result = detect_drift(str(upload_path), mapping)
        return result

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error detecting drift: {exc}")

    finally:
        if upload_path.exists():
            upload_path.unlink()
