from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

import click
import yaml

from src.contracts.test_suite import TestConfig, TestSuiteConfig
from src.utils.params import resolve_params


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
        missing_rows = result.get("missing_rows", 0) or 0
        extra_rows = result.get("extra_rows", 0) or 0
        diff_pct = result.get("different_rows_pct", 0.0) or 0.0

        if thr.max_missing_rows is not None and missing_rows > thr.max_missing_rows:
            failures.append(
                f"missing_rows {missing_rows} exceeds max_missing_rows {thr.max_missing_rows}"
            )
        if thr.max_extra_rows is not None and extra_rows > thr.max_extra_rows:
            failures.append(
                f"extra_rows {extra_rows} exceeds max_extra_rows {thr.max_extra_rows}"
            )
        if thr.max_different_rows_pct is not None and diff_pct > thr.max_different_rows_pct:
            failures.append(
                f"different_rows_pct {diff_pct:.2f}% exceeds max_different_rows_pct {thr.max_different_rows_pct}%"
            )

    if failures:
        return "FAIL", "; ".join(failures)
    return "PASS", ""


def _run_single_test(
    test: TestConfig,
    resolved_file: str,
    output_dir: str,
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
            from src.services.compare_service import run_compare_service

            keys_str = ",".join(test.key_columns) if test.key_columns else None
            svc_result = run_compare_service(
                file1=resolved_file,
                file2=test.oracle_query or "",
                keys=keys_str,
                mapping=test.mapping,
            )
            total_rows = svc_result.get("total_rows", 0) or 0
            error_count = svc_result.get("different_count", 0) or 0
            warning_count = 0
        else:
            raise ValueError(f"Unknown test type: {test.type!r}")

        status, detail = _check_thresholds(test, {
            "error_count": error_count,
            "warning_count": warning_count,
            "missing_rows": svc_result.get("missing_rows", 0) if test.type == "oracle_vs_file" else 0,
            "extra_rows": svc_result.get("extra_rows", 0) if test.type == "oracle_vs_file" else 0,
            "different_rows_pct": svc_result.get("different_rows_pct", 0.0) if test.type == "oracle_vs_file" else 0.0,
        })

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
        result = _run_single_test(test, resolved_file, output_dir)
        results.append(result)

    return results
