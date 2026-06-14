from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import click
import yaml

from src.contracts.test_suite import TestConfig, TestSuiteConfig
from src.reports.renderers.suite_renderer import SuiteReporter
from src.utils.params import resolve_params

try:
    from src.services.run_history_service import write_run_to_db as _db_write_run
except ImportError:  # service not yet present in all environments
    _db_write_run = None  # type: ignore[assignment]

from src.services import baseline_service


def _run_api_check_test(test: TestConfig, params: dict) -> dict:
    """Execute an HTTP API check test.

    Makes an HTTP request to the configured URL and checks the response
    status code and JSON body against expectations.

    Args:
        test: TestConfig with type="api_check".
        params: Resolved parameter dict for variable substitution.

    Returns:
        Result dict with status (PASS/FAIL/ERROR), message, and errors list.
    """
    import httpx

    result: dict[str, Any] = {
        "name": test.name,
        "type": test.type,
        "status": "PASS",
        "errors": [],
        "warnings": [],
    }

    url = resolve_params(test.url or "", params)

    try:
        if test.method and test.method.upper() == "POST":
            resp = httpx.post(url, json=test.body, timeout=test.timeout_seconds)
        else:
            resp = httpx.get(url, timeout=test.timeout_seconds)

        # Check status code
        expected = test.expected_status or 200
        if resp.status_code != expected:
            result["status"] = "FAIL"
            result["errors"].append(
                f"Expected HTTP {expected}, got {resp.status_code}"
            )

        # Check response_contains only when status check passed
        if test.response_contains and result["status"] == "PASS":
            try:
                body = resp.json()
                for key, expected_val in test.response_contains.items():
                    actual = body.get(key)
                    if actual != expected_val:
                        result["status"] = "FAIL"
                        result["errors"].append(
                            f"response['{key}']: expected {expected_val!r}, got {actual!r}"
                        )
            except Exception:
                result["status"] = "FAIL"
                result["errors"].append("Response is not valid JSON")

    except httpx.ConnectError as e:
        result["status"] = "ERROR"
        result["errors"].append(f"Connection failed: {e}")
    except httpx.TimeoutException:
        result["status"] = "ERROR"
        result["errors"].append(f"Request timed out after {test.timeout_seconds}s")
    except Exception as e:
        result["status"] = "ERROR"
        result["errors"].append(f"Unexpected error: {e}")

    return result


def _run_oracle_vs_file_test(
    test: TestConfig, resolved_file: str, output_dir: str, run_id: str
) -> dict[str, Any]:
    """Execute oracle_vs_file test: run SQL query, write temp CSV, compare against batch file.

    The ``__source_row__`` column added by parsers is stripped before the
    Oracle comparison so it does not cause schema mismatches with Oracle
    result sets that do not contain this internal tracking column.

    Args:
        test: Test configuration from the suite YAML.
        resolved_file: Absolute path to the resolved batch file.
        output_dir: Directory where intermediate Oracle CSV is written.
        run_id: Unique identifier for this suite run.

    Returns:
        Dict containing comparison results or an error/skipped sentinel dict.
    """
    if not (os.getenv("ORACLE_USER") and os.getenv("ORACLE_DSN")):
        return {
            "name": test.name,
            "type": test.type,
            "status": "SKIPPED",
            "detail": "Oracle not configured — set ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN",
            "total_rows": 0,
            "error_count": 0,
            "warning_count": 0,
            "duration_seconds": 0.0,
        }

    t0 = time.time()
    try:
        from src.database.connection import OracleConnection
        from src.database.extractor import DataExtractor

        query = test.oracle_query or ""
        if Path(query.strip()).suffix == ".sql" and Path(query.strip()).exists():
            query = Path(query.strip()).read_text()

        conn = OracleConnection.from_env()
        extractor = DataExtractor(conn)
        temp_file = (
            Path(output_dir)
            / f"oracle_{run_id}_{test.name.replace(' ', '_')[:20]}.csv"
        )
        oracle_params = test.oracle_params or {}
        extractor.extract_to_file(query, str(temp_file), params=oracle_params)

        keys_str = ",".join(test.key_columns) if test.key_columns else None

        # Parse the batch file so we can strip __source_row__ before comparing
        # against Oracle data, which does not contain this internal column.
        import json as _json
        import pandas as _pd
        from src.parsers.format_detector import FormatDetector as _FormatDetector
        from src.parsers.fixed_width_parser import FixedWidthParser as _FWParser

        mapping_config = None
        if test.mapping:
            _mpath = test.mapping
            # Resolve relative mapping path if needed
            if not os.path.isabs(_mpath):
                from src.config.loader import ConfigLoader
                try:
                    mapping_config = ConfigLoader().load_mapping(_mpath)
                except Exception:
                    pass
            if mapping_config is None:
                try:
                    with open(_mpath, "r", encoding="utf-8") as _mf:
                        mapping_config = _json.load(_mf)
                except Exception:
                    pass

        _detector = _FormatDetector()
        try:
            _parser_class = _detector.get_parser_class(resolved_file)
        except Exception:
            _parser_class = None

        if _parser_class == _FWParser and mapping_config and mapping_config.get("fields"):
            _specs = []
            _pos = 0
            for _f in mapping_config.get("fields", []):
                _name = _f["name"]
                _len = int(_f["length"])
                _start = int(_f["position"]) - 1 if _f.get("position") is not None else _pos
                _end = _start + _len
                _specs.append((_name, _start, _end))
                _pos = _end
            _parser = _FWParser(resolved_file, _specs)
        elif _parser_class is not None:
            _parser = _parser_class(resolved_file)
        else:
            _parser = None

        if _parser is not None:
            try:
                _df = _parser.parse()
                # Strip the internal row-tracking column before Oracle comparison
                # to prevent schema mismatches with Oracle result sets.
                _df = _df.drop(columns=['__source_row__'], errors='ignore')

                _oracle_df = _pd.read_csv(str(temp_file), dtype=str, keep_default_na=False)

                from src.comparators.file_comparator import FileComparator as _FC
                _key_cols = test.key_columns or None
                _comparator = _FC(_df, _oracle_df, key_columns=_key_cols)
                svc_result = _comparator.compare(detailed=True)
                svc_result["duration_seconds"] = time.time() - t0
                return svc_result
            except Exception:
                pass  # Fall through to the service-level comparison below

        from src.services.compare_service import run_compare_service

        svc_result = run_compare_service(
            file1=resolved_file,
            file2=str(temp_file),
            keys=keys_str,
            mapping=test.mapping,
            detailed=True,
            use_chunked=False,
        )
        svc_result["duration_seconds"] = time.time() - t0
        return svc_result
    except Exception as e:
        return {
            "name": test.name,
            "type": test.type,
            "status": "ERROR",
            "detail": str(e),
            "total_rows": 0,
            "error_count": 0,
            "warning_count": 0,
            "duration_seconds": time.time() - t0,
        }


def _parse_params_str(params_str: str) -> dict[str, str]:
    """Parse 'key=value,key2=value2' string into a dict."""
    result: dict[str, str] = {}
    if not params_str:
        return result
    for pair in params_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Invalid parameter '{pair}': expected key=value format")
        key, _, value = pair.partition("=")
        result[key.strip()] = value.strip()
    return result


def _check_thresholds(test: TestConfig, result: dict[str, Any]) -> tuple[str, str]:
    """Evaluate threshold config against service result.

    Returns (status, detail) where status is 'PASS' or 'FAIL'.
    """
    thr = test.thresholds
    error_count = result.get("error_count", 0) or 0
    warning_count = result.get("warning_count", 0) or 0

    failures = []

    if error_count > thr.max_errors:
        failures.append(
            f"error_count {error_count} exceeds max_errors {thr.max_errors}"
        )

    if thr.max_warnings is not None and warning_count > thr.max_warnings:
        failures.append(
            f"warning_count {warning_count} exceeds max_warnings {thr.max_warnings}"
        )

    if test.type == "oracle_vs_file":
        missing = len(result.get("only_in_file1", []))
        extra = len(result.get("only_in_file2", []))
        total = max(result.get("total_rows_file1", 1), 1)
        diff_rows = result.get("rows_with_differences", 0)
        diff_pct = (diff_rows / total) * 100

        if thr.max_missing_rows is not None and missing > thr.max_missing_rows:
            failures.append(
                f"missing_rows={missing} exceeds max={thr.max_missing_rows}"
            )
        if thr.max_extra_rows is not None and extra > thr.max_extra_rows:
            failures.append(
                f"extra_rows={extra} exceeds max={thr.max_extra_rows}"
            )
        if thr.max_different_rows_pct is not None and diff_pct > thr.max_different_rows_pct:
            failures.append(
                f"different_rows_pct={diff_pct:.2f}% exceeds max={thr.max_different_rows_pct}%"
            )

    if failures:
        return "FAIL", "; ".join(failures)
    return "PASS", ""


def _run_single_test(
    test: TestConfig,
    resolved_file: str,
    output_dir: str,
    run_id: str = "",
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one test and return its result dict."""
    start = time.monotonic()
    report_path = None
    status = "ERROR"
    detail = ""
    total_rows = 0
    error_count = 0
    warning_count = 0

    try:
        os.makedirs(output_dir, exist_ok=True)
        safe_name = test.name.replace(" ", "_").replace("/", "_")
        report_filename = f"{safe_name}.html"
        report_path = str(Path(output_dir) / report_filename)

        if test.type in ("structural", "rules"):
            from src.services.validate_service import run_validate_service

            svc_result = run_validate_service(
                file=resolved_file,
                mapping=test.mapping,
                rules=test.rules,
                output=report_path,
            )
            total_rows = svc_result.get("total_rows", 0) or 0
            error_count = svc_result.get("error_count", 0) or 0
            warning_count = svc_result.get("warning_count", 0) or 0

        elif test.type == "oracle_vs_file":
            svc_result = _run_oracle_vs_file_test(test, resolved_file, output_dir, run_id)
            # If oracle extraction/config returned an ERROR or SKIPPED dict, propagate immediately
            if svc_result.get("status") in ("ERROR", "SKIPPED"):
                return {
                    "name": test.name,
                    "type": test.type,
                    "status": svc_result["status"],
                    "total_rows": svc_result.get("total_rows", 0),
                    "error_count": svc_result.get("error_count", 0),
                    "warning_count": svc_result.get("warning_count", 0),
                    "duration_seconds": round(time.monotonic() - start, 1),
                    "report_path": report_path,
                    "detail": svc_result.get("detail", ""),
                }
            total_rows = svc_result.get("total_rows_file1", 0) or 0
            error_count = svc_result.get("rows_with_differences", 0) or 0
            warning_count = 0

        elif test.type == "api_check":
            # Resolve params dict for variable substitution in URL
            merged: dict[str, Any] = dict(params) if params is not None else {}
            api_result = _run_api_check_test(test, merged)
            return {
                "name": test.name,
                "type": test.type,
                "status": api_result["status"],
                "total_rows": 0,
                "error_count": len(api_result.get("errors", [])),
                "warning_count": 0,
                "duration_seconds": round(time.monotonic() - start, 1),
                "report_path": None,
                "detail": "; ".join(api_result.get("errors", [])),
            }

        else:
            raise ValueError(f"Unknown test type: {test.type!r}")

        threshold_input = {"error_count": error_count, "warning_count": warning_count}
        if test.type == "oracle_vs_file":
            threshold_input.update(svc_result)
        status, detail = _check_thresholds(test, threshold_input)

    except Exception as exc:
        status = "ERROR"
        detail = str(exc)
        report_path = None

    duration = time.monotonic() - start
    return {
        "name": test.name,
        "type": test.type,
        "status": status,
        "total_rows": total_rows,
        "error_count": error_count,
        "warning_count": warning_count,
        "duration_seconds": round(duration, 1),
        "report_path": report_path,
        "detail": detail,
    }


def _compute_overall_status(results: list[dict[str, Any]]) -> str:
    """Derive PASS / PARTIAL / FAIL from a list of per-test result dicts."""
    statuses = {r["status"] for r in results}
    error_statuses = {"FAIL", "ERROR"}
    pass_statuses = {"PASS", "SKIPPED"}

    if statuses <= pass_statuses:
        overall = "PASS"
    elif statuses & error_statuses and not (statuses - error_statuses - pass_statuses):
        overall = "PARTIAL" if statuses & pass_statuses else "FAIL"
    else:
        overall = "PARTIAL"

    if not (statuses & pass_statuses):
        overall = "FAIL"
    return overall


def _append_run_history(
    output_dir: str,
    run_id: str,
    suite: Any,
    results: list[dict[str, Any]],
    suite_report_path: str,
    env: str,
    archive_path: str = "",
    timestamp: str = "",
) -> None:
    """Append a run summary entry to ``reports/run_history.json``.

    Resolves ``overall_status`` from the individual test results:

    * ``PASS`` — every test passed or was skipped.
    * ``FAIL`` — at least one test has status ``ERROR``.
    * ``PARTIAL`` — a mix of passing and failing tests (no ERRORs).

    The history file is created if it does not exist.  Corrupt JSON is
    silently replaced with a fresh list containing only the new entry.

    Args:
        output_dir: Directory where the suite HTML report was written.
            Used to resolve the history file path one level up.
        run_id: Unique identifier for this suite run.
        suite: The ``TestSuiteConfig`` instance for the run.
        results: List of per-test result dicts returned by ``_run_single_test``.
        suite_report_path: Absolute path to the suite-level HTML report file.
        env: Effective environment string (resolved from CLI flag or suite config).
        archive_path: Absolute path to the archive directory created by
            ``ArchiveManager.archive_run``.  Empty string when archiving was
            skipped or not yet implemented.
        timestamp: ISO-8601 UTC timestamp string for this run entry.  When
            provided, it is shared with the archive manifest so both records
            carry the same timestamp.  Falls back to ``datetime.utcnow()`` if
            not supplied.

    Note:
        ``quality_score`` is extracted from the first result that carries a
        non-``None`` ``quality_score`` value.  It is stored as ``None`` when
        no result contains this field.  The value is written to both the JSON
        history file and the DB entry (when Oracle is configured).
    """
    history_path = Path(output_dir) / ".." / "reports" / "run_history.json"
    history_path = history_path.resolve()
    history_path.parent.mkdir(parents=True, exist_ok=True)

    overall_status = _compute_overall_status(results)

    # Aggregate quality_score: use the first non-None value found in results,
    # or None when no result carries a quality_score.
    quality_score: float | None = next(
        (r["quality_score"] for r in results if r.get("quality_score") is not None),
        None,
    )

    entry: dict[str, Any] = {
        "run_id": run_id,
        "suite_name": suite.name,
        "environment": env,
        "timestamp": timestamp or (datetime.utcnow().isoformat() + "Z"),
        "status": overall_status,
        "report_url": f"/reports/{Path(suite_report_path).name}",
        "pass_count":  sum(1 for r in results if r["status"] == "PASS"),
        "fail_count":  sum(1 for r in results if r["status"] in ("FAIL", "ERROR")),
        "skip_count":  sum(1 for r in results if r["status"] == "SKIPPED"),
        "total_count": len(results),
        "archive_path": archive_path,
        "quality_score": quality_score,
    }

    existing: list[dict[str, Any]] = []
    if history_path.exists():
        try:
            existing = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "run_history.json is unreadable, starting fresh: %s", exc
            )
            existing = []
    existing.append(entry)
    history_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    # Dual-write to Oracle when ORACLE_USER is configured.
    if os.getenv("ORACLE_USER") and _db_write_run is not None:
        try:
            _db_write_run(entry, run_id, results)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).warning(
                "run_history DB write failed (JSON fallback still written): %s", exc
            )


def _build_suite_result(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a minimal result dict for baseline_service from per-test results.

    Aggregates pass/fail/total counts and the first non-None quality_score
    across all tests so that :func:`~src.services.baseline_service.update_baseline`
    receives the fields it expects.

    Args:
        results: Per-test result dicts returned by :func:`_run_single_test`.

    Returns:
        Dict with keys: ``pass_count``, ``total_count``, ``invalid_rows``,
        ``total_rows``, and optionally ``quality_score``.
    """
    pass_count = sum(1 for r in results if r.get("status") == "PASS")
    total_count = len(results)
    invalid_rows = sum(r.get("error_count", 0) or 0 for r in results)
    total_rows = sum(r.get("row_count", 0) or 0 for r in results)
    quality_score: float | None = next(
        (r["quality_score"] for r in results if r.get("quality_score") is not None),
        None,
    )
    suite_result: dict[str, Any] = {
        "pass_count": pass_count,
        "total_count": total_count,
        "invalid_rows": invalid_rows,
        "total_rows": total_rows,
    }
    if quality_score is not None:
        suite_result["quality_score"] = quality_score
    return suite_result


def run_suite_from_path(
    suite_path: str,
    params: dict[str, str],
    env: str,
    output_dir: str,
) -> list[dict[str, Any]]:
    """Run a test suite from a YAML file path with a pre-built params dict.

    This is a thin wrapper around the core suite-run logic that accepts a
    ready-made ``params`` dict (rather than a raw ``params_str`` string).
    Both the file-watcher and the webhook trigger endpoint call this function,
    ensuring CLI/API parity while keeping the CLI ``run-tests`` command intact.

    Args:
        suite_path: Absolute or relative path to the suite YAML file.
        params: Dict of substitution parameters (e.g. ``{"run_date": "20260301"}``).
        env: Environment name (e.g. ``"dev"``, ``"staging"``).
        output_dir: Directory where HTML reports and run history are written.

    Returns:
        List of per-test result dicts (see :func:`_run_single_test`).
    """
    with open(suite_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    suite = TestSuiteConfig(**raw)

    run_id = str(uuid.uuid4())
    merged_params = {
        "run_id": run_id,
        "environment": env or suite.environment,
        **params,
    }

    results: list[dict[str, Any]] = []
    for test in suite.tests:
        resolved_file = resolve_params(test.file or "", merged_params)
        result = _run_single_test(
            test, resolved_file, output_dir, run_id=run_id, params=merged_params
        )
        results.append(result)

    safe_suite_name = re.sub(r"[^\w\-]", "_", suite.name)
    suite_report_path = str(
        Path(output_dir) / f"{safe_suite_name}_{run_id}_suite.html"
    )
    os.makedirs(output_dir, exist_ok=True)
    SuiteReporter().generate(
        suite_name=suite.name,
        results=results,
        output_path=suite_report_path,
        run_id=run_id,
        environment=env or suite.environment,
    )

    # Local import to avoid loading ArchiveManager at module level during CLI startup.
    from src.utils.archive import ArchiveManager

    run_timestamp = datetime.utcnow().isoformat() + "Z"
    overall_status = _compute_overall_status(results)
    archive_path_str = ""
    try:
        archive = ArchiveManager()
        report_files = [suite_report_path] + [
            r["report_path"] for r in results if r.get("report_path")
        ]
        archive_run_dir = archive.archive_run(
            run_id=run_id,
            suite_name=suite.name,
            env=env or suite.environment,
            timestamp=run_timestamp,
            files=report_files,
            status=overall_status,
        )
        archive_path_str = str(archive_run_dir)
    except Exception as exc:
        click.echo(f"[archive] warning: could not archive run {run_id}: {exc}", err=True)

    _append_run_history(
        output_dir=output_dir,
        run_id=run_id,
        suite=suite,
        results=results,
        suite_report_path=suite_report_path,
        env=env or suite.environment,
        archive_path=archive_path_str,
        timestamp=run_timestamp,
    )

    try:
        baseline_service.update_baseline(suite.name, _build_suite_result(results))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("baseline update failed: %s", exc)

    return results


def run_tests_command(
    suite_path: str,
    params_str: str,
    env: str,
    output_dir: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Load suite YAML, resolve params, run each test, return results list."""

    with open(suite_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    suite = TestSuiteConfig(**raw)

    # Build param dict: generate a single run_id for the whole suite.
    run_id = str(uuid.uuid4())
    user_params = _parse_params_str(params_str)
    params = {
        "run_id": run_id,
        "environment": env or suite.environment,
        **user_params,
    }

    if dry_run:
        click.echo(f"[dry-run] Suite: {suite.name}")
        click.echo(f"[dry-run] Environment: {env or suite.environment}")
        click.echo(f"[dry-run] run_id: {run_id}")
        for test in suite.tests:
            resolved_file = resolve_params(test.file or "", params)
            click.echo(
                f"[dry-run]   test={test.name!r}  type={test.type}"
                f"  file={resolved_file!r}  mapping={test.mapping!r}"
            )
        return []

    return run_suite_from_path(suite_path, params=user_params, env=env, output_dir=output_dir)
