"""Unit tests for the ``run_suite`` MCP tool payload (S22-5, #442).

Exercises :func:`src.mcp.suite_tools.run_suite_payload` directly (not over the
MCP transport — that is the integration test's job). The tool is a THIN wrapper
over the existing suite runner
(:func:`src.commands.run_tests_command.run_suite_from_path`), the same code path
the ``valdo run-tests`` CLI command drives. These tests pin:

1. A happy-path file-based (no Oracle) structural suite returns a summary with
   per-test pass/fail entries and an overall verdict.
2. A missing suite file is a caller-fixable :class:`ToolError`.
3. A blank suite path is a caller-fixable :class:`ToolError`.
4. The description constant is non-empty and names the tool.

The fixture is a single ``structural`` test against a tiny pipe-delimited file
and a minimal mapping — it runs without any database. Reports are written under
``tmp_path`` so the test never touches the repo ``reports/`` tree.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from src.mcp.suite_tools import RUN_SUITE_DESCRIPTION, run_suite_payload


# ---------------------------------------------------------------------------
# Fixture helpers — a self-contained, database-free structural suite.
# ---------------------------------------------------------------------------

_MAPPING = {
    "mapping_name": "s22_5_customers",
    "version": "1.0.0",
    "source": {"type": "file", "format": "pipe_delimited"},
    "target": {"type": "database", "table_name": "CUSTOMER"},
    "mappings": [
        {
            "source_column": "customer_id",
            "target_column": "CUSTOMER_ID",
            "data_type": "string",
            "required": True,
            "validation_rules": [{"type": "not_null"}],
        },
        {
            "source_column": "name",
            "target_column": "NAME",
            "data_type": "string",
            "required": True,
            "validation_rules": [{"type": "not_null"}],
        },
    ],
    "key_columns": ["customer_id"],
}

_DATA = "customer_id|name\nCUST01|alice\nCUST02|bob\n"


def _write_suite(tmp_path: Path, *, clean: bool = True) -> Path:
    """Write a database-free suite + its data/mapping under tmp_path.

    Args:
        tmp_path: pytest temp directory.
        clean: When True the suite is a single passing structural test. When
            False a second ``api_check`` test points at a closed local port so
            the connection fails fast (ERROR) — exercising a non-PASS result
            without any database or network dependency.

    Returns:
        Path to the suite YAML file.
    """
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(_MAPPING), encoding="utf-8")

    data_path = tmp_path / "customers.psv"
    data_path.write_text(_DATA, encoding="utf-8")

    extra = ""
    if not clean:
        # Port 1 is reserved/unbindable on every platform; the api_check test
        # gets a fast ConnectError -> status ERROR (deterministic, offline).
        extra = (
            "  - name: Unreachable Health Check\n"
            "    type: api_check\n"
            "    url: http://127.0.0.1:1/health\n"
            "    expected_status: 200\n"
            "    timeout_seconds: 2\n"
        )

    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        textwrap.dedent(
            f"""\
            name: S22-5 File Suite
            environment: dev
            tests:
              - name: Customer Structure Check
                type: structural
                file: {data_path}
                mapping: {mapping_path}
                thresholds:
                  max_errors: 0
            """
        )
        + extra,
        encoding="utf-8",
    )
    return suite_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_suite_returns_summary(tmp_path):
    """A clean file-based structural suite returns a PASS summary."""
    suite_path = _write_suite(tmp_path, clean=True)
    out_dir = tmp_path / "reports"

    payload = run_suite_payload(
        suite=str(suite_path),
        env="dev",
        output_dir=str(out_dir),
    )

    assert payload["suite"] == "S22-5 File Suite"
    assert payload["overall_status"] == "PASS"
    assert payload["total_count"] == 1
    assert payload["pass_count"] == 1
    assert payload["fail_count"] == 0
    assert isinstance(payload["tests"], list) and len(payload["tests"]) == 1
    test = payload["tests"][0]
    assert test["name"] == "Customer Structure Check"
    assert test["status"] == "PASS"


def test_run_suite_failing_test_is_a_result_not_an_error(tmp_path):
    """A test that breaches its threshold is a RESULT (overall FAIL), not a ToolError."""
    suite_path = _write_suite(tmp_path, clean=False)
    out_dir = tmp_path / "reports"

    payload = run_suite_payload(suite=str(suite_path), output_dir=str(out_dir))

    assert payload["overall_status"] in ("FAIL", "PARTIAL")
    assert payload["fail_count"] >= 1
    statuses = {t["status"] for t in payload["tests"]}
    assert statuses & {"FAIL", "ERROR"}, payload


def test_run_suite_missing_file_raises_tool_error(tmp_path):
    """A non-existent suite path is a caller-fixable ToolError."""
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ToolError, match="not found"):
        run_suite_payload(suite=str(missing), output_dir=str(tmp_path))


def test_run_suite_blank_suite_raises_tool_error():
    """A blank suite path is a caller-fixable ToolError."""
    with pytest.raises(ToolError, match="required"):
        run_suite_payload(suite="")


def test_run_suite_malformed_yaml_raises_tool_error(tmp_path):
    """A suite file that fails schema validation is a caller-fixable ToolError."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\ntests: not-a-list\n", encoding="utf-8")
    with pytest.raises(ToolError):
        run_suite_payload(suite=str(bad), output_dir=str(tmp_path))


def test_description_constant_is_present():
    """The advertised description is non-empty and names the tool surface."""
    assert isinstance(RUN_SUITE_DESCRIPTION, str)
    assert RUN_SUITE_DESCRIPTION
    assert "suite" in RUN_SUITE_DESCRIPTION.lower()
