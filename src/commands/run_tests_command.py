from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

import click
import yaml

from src.contracts.test_suite import TestConfig, TestSuiteConfig
from src.reports.renderers.suite_renderer import SuiteReporter
from src.utils.params import resolve_params


def _run_oracle_vs_file_test(
    test: TestConfig, resolved_file: str, output_dir: str, run_id: str
) -> dict[str, Any]:
    """Execute oracle_vs_file test: run SQL query, write temp CSV, compare against batch file."""
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
            resolved_file = resolve_params(test.file, params)
            click.echo(
                f"[dry-run]   test={test.name!r}  type={test.type}"
                f"  file={resolved_file!r}  mapping={test.mapping!r}"
            )
        return []

    results: list[dict[str, Any]] = []
    for test in suite.tests:
        resolved_file = resolve_params(test.file, params)
        result = _run_single_test(test, resolved_file, output_dir, run_id=run_id)
        results.append(result)

    # Write the suite-level HTML summary report.
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
    click.echo(f"Suite report written to: {suite_report_path}")

    return results
