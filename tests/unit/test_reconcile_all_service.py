"""Parity tests for the extracted reconcile-all service (S16-3, #421).

These tests are the parity oracle for moving the ``reconcile-all`` workflow
(the per-file parse/reconcile loop, summary aggregation, and the baseline
drift-diff engine) out of the inlined ``src/main.py`` command into
:func:`src.services.reconcile_all_service.reconcile_all_service`.

Strategy:

* Build a small fixture *mappings dir* (real JSON files under pytest
  ``tmp_path``) targeting a real SQLite database (also under ``tmp_path`` —
  never a tracked DB).
* Re-implement the OLD inlined reconcile-all loop + drift-diff *inside the
  test* as the parity oracle, then assert the service returns an identical
  structure (same per-mapping verdicts, same summary, same drift-diff).

No new deps, parameterized SQL only (DDL here is fixed-string CREATE TABLE),
no tracked fixture DBs.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest
from click.testing import CliRunner

from src.config.loader import ConfigLoader
from src.config.mapping_parser import MappingParser
from src.database.adapters.sqlite_adapter import SQLiteAdapter
from src.database.reconciliation import SchemaReconciler
from src.services.reconcile_all_service import reconcile_all_service


# ---------------------------------------------------------------------------
# Fixtures: a real SQLite DB + a directory of mapping JSON files
# ---------------------------------------------------------------------------


def _seed_target_db(db_path: str) -> None:
    """Create a CUSTOMER target table with mixed, cleanly-mappable types."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE CUSTOMER (
                CUSTOMER_ID TEXT NOT NULL,
                AGE INTEGER,
                BALANCE NUMERIC,
                IS_ACTIVE BOOLEAN,
                NOTES
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _clean_mapping_dict(name: str) -> Dict[str, Any]:
    """A mapping that reconciles VALID/clean against CUSTOMER."""
    return {
        "mapping_name": name,
        "version": "1.0.0",
        "description": "clean mapping",
        "source": {"type": "file", "format": "pipe_delimited"},
        "target": {"type": "database", "table_name": "CUSTOMER"},
        "mappings": [
            {
                "source_column": "customer_id",
                "target_column": "CUSTOMER_ID",
                "data_type": "string",
                "required": True,
                "transformations": [],
                "validation_rules": [],
            },
            {
                "source_column": "age",
                "target_column": "AGE",
                "data_type": "integer",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            },
        ],
        "key_columns": ["customer_id"],
    }


def _error_mapping_dict(name: str) -> Dict[str, Any]:
    """A mapping with a required column missing from the table -> hard error."""
    d = _clean_mapping_dict(name)
    d["mappings"].append(
        {
            "source_column": "ssn",
            "target_column": "SSN",  # not in CUSTOMER
            "data_type": "string",
            "required": True,
            "transformations": [],
            "validation_rules": [],
        }
    )
    return d


@pytest.fixture()
def fixture_env(tmp_path: Path, monkeypatch):
    """Seed a SQLite DB + a mappings dir; point DB_ADAPTER/DB_PATH at them.

    Returns:
        Tuple of (mappings_dir: str, pattern: str).
    """
    db_path = str(tmp_path / "recon_all_fixture.db")
    _seed_target_db(db_path)

    mappings_dir = tmp_path / "mappings"
    mappings_dir.mkdir()
    # Two clean mappings + one with a missing-required-column error.
    (mappings_dir / "a_clean.json").write_text(
        json.dumps(_clean_mapping_dict("a_clean")), encoding="utf-8"
    )
    (mappings_dir / "b_clean.json").write_text(
        json.dumps(_clean_mapping_dict("b_clean")), encoding="utf-8"
    )
    (mappings_dir / "c_error.json").write_text(
        json.dumps(_error_mapping_dict("c_error")), encoding="utf-8"
    )

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", db_path)

    return str(mappings_dir), "*.json"


# ---------------------------------------------------------------------------
# The OLD inlined logic, re-implemented here as the parity oracle
# ---------------------------------------------------------------------------


def _old_inlined_reconcile_all(
    mappings_dir: str, pattern: str, baseline: str | None = None
) -> Dict[str, Any]:
    """Faithful copy of the pre-S16-3 inlined ``main.py`` reconcile-all body.

    Mirrors src/main.py reconcile_all (~:417-565) sans the click.echo output:
    same adapter construction, per-file loop, summary aggregation, and
    drift-diff. This is the parity oracle the service must match exactly.
    """
    adapter = SchemaReconciler  # placeholder to keep import usage explicit
    from src.database.adapters.factory import get_database_adapter

    loader = ConfigLoader()
    parser = MappingParser()
    db_adapter = get_database_adapter()
    reconciler = SchemaReconciler(db_adapter)

    mapping_files = sorted(Path(mappings_dir).glob(pattern))

    results: List[Dict[str, Any]] = []
    total_errors = 0
    total_warnings = 0
    invalid_mappings = 0

    db_adapter.connect()
    for mapping_file in mapping_files:
        try:
            mapping_dict = loader.load_mapping(str(mapping_file))
            mapping_doc = parser.parse(mapping_dict)
            result = reconciler.reconcile_mapping(mapping_doc)

            errors = result.get("error_count", len(result.get("errors", [])))
            warnings = result.get("warning_count", len(result.get("warnings", [])))
            total_errors += errors
            total_warnings += warnings

            if not result.get("valid", False):
                invalid_mappings += 1

            results.append(
                {
                    "mapping_file": str(mapping_file),
                    "mapping_name": mapping_doc.mapping_name,
                    **result,
                }
            )
        except Exception as file_error:  # noqa: BLE001 — parity with old code
            invalid_mappings += 1
            total_errors += 1
            results.append(
                {
                    "mapping_file": str(mapping_file),
                    "valid": False,
                    "errors": [f"Failed to process mapping: {file_error}"],
                    "warnings": [],
                    "error_count": 1,
                    "warning_count": 0,
                }
            )
    db_adapter.disconnect()

    summary = {
        "total_mappings": len(mapping_files),
        "valid_mappings": len(mapping_files) - invalid_mappings,
        "invalid_mappings": invalid_mappings,
        "total_errors": total_errors,
        "total_warnings": total_warnings,
        "results": results,
    }

    drift = None
    if baseline:
        with open(baseline, "r") as f:
            baseline_report = json.load(f)
        baseline_results = {
            r.get("mapping_file"): r
            for r in baseline_report.get("results", [])
            if r.get("mapping_file")
        }
        current_results = {
            r.get("mapping_file"): r for r in results if r.get("mapping_file")
        }
        baseline_files = set(baseline_results.keys())
        current_files = set(current_results.keys())
        added_files = sorted(current_files - baseline_files)
        removed_files = sorted(baseline_files - current_files)
        changed = []
        new_errors = 0
        new_warnings = 0
        for mf in sorted(current_files & baseline_files):
            old = baseline_results[mf]
            new = current_results[mf]
            old_e = old.get("error_count", len(old.get("errors", [])))
            old_w = old.get("warning_count", len(old.get("warnings", [])))
            new_e = new.get("error_count", len(new.get("errors", [])))
            new_w = new.get("warning_count", len(new.get("warnings", [])))
            delta_e = new_e - old_e
            delta_w = new_w - old_w
            if delta_e != 0 or delta_w != 0:
                changed.append(
                    {
                        "mapping_file": mf,
                        "old_errors": old_e,
                        "new_errors": new_e,
                        "delta_errors": delta_e,
                        "old_warnings": old_w,
                        "new_warnings": new_w,
                        "delta_warnings": delta_w,
                    }
                )
                if delta_e > 0:
                    new_errors += delta_e
                if delta_w > 0:
                    new_warnings += delta_w
        drift = {
            "baseline": baseline,
            "added_files": added_files,
            "removed_files": removed_files,
            "changed": changed,
            "new_errors": new_errors,
            "new_warnings": new_warnings,
        }

    if drift is not None:
        summary["drift"] = drift

    return summary


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


def test_service_matches_old_inlined_no_baseline(fixture_env):
    """Service result EQUALS the old inlined path: same verdicts + summary."""
    mappings_dir, pattern = fixture_env

    expected = _old_inlined_reconcile_all(mappings_dir, pattern)
    actual = reconcile_all_service(mappings_dir=mappings_dir, pattern=pattern)

    assert actual == expected


def test_service_aggregation_values(fixture_env):
    """Sanity check on the aggregate counts (2 clean + 1 error mapping)."""
    mappings_dir, pattern = fixture_env

    result = reconcile_all_service(mappings_dir=mappings_dir, pattern=pattern)

    assert result["total_mappings"] == 3
    assert result["invalid_mappings"] == 1
    assert result["valid_mappings"] == 2
    assert result["total_errors"] >= 1
    assert "drift" not in result


def test_service_empty_dir_returns_empty_summary(tmp_path, monkeypatch):
    """No matching mapping files -> zeroed summary, empty results, no drift."""
    db_path = str(tmp_path / "empty.db")
    _seed_target_db(db_path)
    empty_dir = tmp_path / "empty_mappings"
    empty_dir.mkdir()
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", db_path)

    result = reconcile_all_service(mappings_dir=str(empty_dir), pattern="*.json")

    assert result["total_mappings"] == 0
    assert result["results"] == []
    assert result["total_errors"] == 0


def test_service_drift_diff_parity(fixture_env, tmp_path):
    """Baseline-drift scenario: drift-diff matches the old inlined engine.

    Builds a baseline report where ``c_error`` had zero errors, so the current
    run (1 error) registers as drift (new error + a changed entry). Asserts the
    service's drift block equals the old inlined drift engine's output exactly.
    """
    mappings_dir, pattern = fixture_env

    # Produce a baseline = current run, then mutate it so c_error looks clean
    # in the baseline -> current run drifts (a new error appears).
    baseline_summary = reconcile_all_service(mappings_dir=mappings_dir, pattern=pattern)
    for r in baseline_summary["results"]:
        if r["mapping_file"].endswith("c_error.json"):
            r["error_count"] = 0
            r["errors"] = []
            r["valid"] = True
    baseline_path = str(tmp_path / "baseline.json")
    with open(baseline_path, "w") as f:
        json.dump(baseline_summary, f)

    expected = _old_inlined_reconcile_all(mappings_dir, pattern, baseline=baseline_path)
    actual = reconcile_all_service(
        mappings_dir=mappings_dir, pattern=pattern, baseline=baseline_path
    )

    assert actual == expected
    # And the drift specifically reflects the seeded regression.
    assert "drift" in actual
    assert actual["drift"]["new_errors"] >= 1
    assert any(
        c["mapping_file"].endswith("c_error.json") and c["delta_errors"] >= 1
        for c in actual["drift"]["changed"]
    )


def test_cli_reconcile_all_report_matches_service(fixture_env, tmp_path):
    """CLI smoke: ``reconcile-all`` writes a report identical to the service.

    Invokes the thin CLI delegator and compares its written aggregate report
    against a direct service call over the same fixture — proving behaviour
    parity end-to-end (no business logic was lost in main.py).
    """
    from src.main import cli

    mappings_dir, pattern = fixture_env
    report_path = str(tmp_path / "cli_report.json")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "reconcile-all",
            "--mappings-dir",
            mappings_dir,
            "--pattern",
            pattern,
            "--output",
            report_path,
        ],
    )

    # One mapping is invalid by design, so the command exits non-zero (1).
    assert result.exit_code == 1, result.output
    assert "RECONCILE-ALL SUMMARY" in result.output

    with open(report_path) as f:
        cli_report = json.load(f)

    service_report = reconcile_all_service(mappings_dir=mappings_dir, pattern=pattern)
    assert cli_report == service_report
