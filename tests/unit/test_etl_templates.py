"""Unit tests for the BA-facing ETL templates under ``templates/etl/``.

These tests pin two contracts:

1. **The CSV-to-CSV reconciliation template (S6-2, #373) validates through
   :class:`src.pipeline.etl_config.SourceConfig`.**  The committed YAML
   under ``templates/etl/csv_file_comparison.yml`` must load through
   ``SourceConfig.model_validate(...)`` so BAs editing the file get the
   same schema enforcement the per-source YAMLs in
   ``config/e2e/sources/`` enjoy.  This catches typos in required keys
   (``source``, ``schema_version``, ``input_files[].file_type``,
   ``input_files[].glob``, ``input_files[].mapping``,
   ``input_files[].target_staging_table``) before a BA tries to run the
   template against production data.

2. **The sample under ``templates/etl/csv_file_comparison_sample/`` round-
   trips through the comparison engine and produces a non-empty violations
   list.**  This is the test side of the README's worked example -- the
   sample is intentionally constructed so that one row matches on the key
   but differs on a non-key column (so ``differences`` is non-empty), one
   row exists only on the left, and one row exists only on the right.
   The expected report shape is fixed at commit time in
   ``expected_report.json``; this test asserts the engine reproduces it.

The template-level test runs entirely against ``SourceConfig.model_validate``
(no filesystem I/O for the *referenced* paths -- only the YAML itself is
read).  The round-trip test bypasses the file format detector (which
currently routes ``.csv`` to the pipe-delimited parser) and exercises
:class:`src.comparators.file_comparator.FileComparator` directly against
DataFrames built with ``pandas.read_csv`` -- the comparison engine is the
unit under test, not the format detector.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from src.comparators.file_comparator import FileComparator
from src.pipeline.etl_config import SourceConfig

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = REPO_ROOT / "templates" / "etl"
CSV_COMPARE_TEMPLATE = TEMPLATES_DIR / "csv_file_comparison.yml"
CSV_COMPARE_SAMPLE_DIR = TEMPLATES_DIR / "csv_file_comparison_sample"
SAMPLE_LEFT = CSV_COMPARE_SAMPLE_DIR / "left.csv"
SAMPLE_RIGHT = CSV_COMPARE_SAMPLE_DIR / "right.csv"
SAMPLE_EXPECTED_REPORT = CSV_COMPARE_SAMPLE_DIR / "expected_report.json"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_csv_compare_template_validates() -> None:
    """``csv_file_comparison.yml`` loads cleanly through ``SourceConfig``.

    Asserts the template YAML parses through the per-source Pydantic
    contract.  The committed template carries ``<FILL_IN_*>`` placeholders
    -- those are intentionally string literals so the template validates
    without modification and BAs see schema errors immediately if they
    misspell a key while replacing the placeholders.
    """
    assert CSV_COMPARE_TEMPLATE.exists(), (
        f"CSV compare template missing at {CSV_COMPARE_TEMPLATE}"
    )

    with CSV_COMPARE_TEMPLATE.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    cfg = SourceConfig.model_validate(raw)

    # Required identity fields survive validation.
    assert cfg.source == "<FILL_IN_SOURCE_NAME>"
    assert cfg.schema_version == 1

    # Both input file entries land in the list with the LEFT/RIGHT labels
    # the README documents.
    assert len(cfg.input_files) == 2
    file_types = [entry.file_type for entry in cfg.input_files]
    assert file_types == ["LEFT", "RIGHT"]

    # The comparison block is preserved as an extra key (SourceConfig has
    # extra="allow"), so the comparison-specific metadata is round-trippable.
    extras = cfg.model_dump()
    assert "comparison" in extras
    assert "key_columns" in extras["comparison"]
    assert "tolerance" in extras


def test_csv_compare_sample_round_trips() -> None:
    """The sample CSVs produce the documented diff through the engine.

    Drives :class:`FileComparator` directly against DataFrames built with
    ``pandas.read_csv`` (bypassing the format detector, which currently
    routes ``.csv`` extensions through the pipe-delimited parser).  Asserts
    the produced report matches ``expected_report.json`` on its load-bearing
    fields:

    * ``differences`` is non-empty (the worked example deliberately puts a
      field-level diff on CUST000004's EMAIL),
    * exactly one row each is reported in ``only_in_file1`` and
      ``only_in_file2`` (CUST000005 only on the left, CUST000006 only on
      the right),
    * the field-level diff lives on the documented column, and
    * the totals on each side match the 5-row sample.
    """
    assert SAMPLE_LEFT.exists(), f"Sample left.csv missing at {SAMPLE_LEFT}"
    assert SAMPLE_RIGHT.exists(), f"Sample right.csv missing at {SAMPLE_RIGHT}"
    assert SAMPLE_EXPECTED_REPORT.exists(), (
        f"Sample expected_report.json missing at {SAMPLE_EXPECTED_REPORT}"
    )

    df_left = pd.read_csv(SAMPLE_LEFT, dtype=str, keep_default_na=False)
    df_right = pd.read_csv(SAMPLE_RIGHT, dtype=str, keep_default_na=False)

    comparator = FileComparator(df_left, df_right, key_columns=["CUSTOMER_ID"])
    result = comparator.compare(detailed=True)

    # Load the documented expected report and pin the load-bearing fields.
    with SAMPLE_EXPECTED_REPORT.open("r", encoding="utf-8") as fh:
        expected = json.load(fh)

    # Totals match the worked-example design (5 rows on each side; 3 match
    # exactly on the key + all non-key fields).
    assert result["total_rows_file1"] == expected["total_rows_file1"] == 5
    assert result["total_rows_file2"] == expected["total_rows_file2"] == 5
    assert result["matching_rows"] == expected["matching_rows"] == 3

    # Non-empty violation list -- this is the acceptance-criteria assertion
    # called out in the S6-2 spec.
    assert len(result["differences"]) > 0, (
        "Expected at least one row-level difference in the worked example"
    )
    assert result["rows_with_differences"] == expected["rows_with_differences"] == 1
    assert result["differences"][0]["keys"]["CUSTOMER_ID"] == "CUST000004"
    assert "EMAIL" in result["differences"][0]["differences"]

    # only-in-left / only-in-right are each populated with one documented row.
    only_left = result["only_in_file1"]
    only_right = result["only_in_file2"]
    only_left_records = (
        only_left.to_dict(orient="records") if hasattr(only_left, "to_dict") else only_left
    )
    only_right_records = (
        only_right.to_dict(orient="records") if hasattr(only_right, "to_dict") else only_right
    )
    assert len(only_left_records) == 1
    assert len(only_right_records) == 1
    assert only_left_records[0]["CUSTOMER_ID"] == "CUST000005"
    assert only_right_records[0]["CUSTOMER_ID"] == "CUST000006"

    # Field-level statistics name the differing column.
    assert result["field_statistics"]["most_different_field"] == "EMAIL"
