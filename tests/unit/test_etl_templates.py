"""Unit tests for the BA-facing ETL templates under ``templates/etl/``.

These tests pin the contracts for each committed template:

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

2. **The CSV sample under ``templates/etl/csv_file_comparison_sample/``
   round-trips through the comparison engine and produces a non-empty
   violations list.**  This is the test side of the README's worked
   example -- the sample is intentionally constructed so that one row
   matches on the key but differs on a non-key column (so ``differences``
   is non-empty), one row exists only on the left, and one row exists
   only on the right.  The expected report shape is fixed at commit time
   in ``expected_report.json``; this test asserts the engine reproduces
   it.

3. **The fixed-width single-record template (S6-3, #374) validates
   through :class:`SourceConfig`.**  Same pattern as the CSV template:
   the committed YAML loads without modification (the ``<FILL_IN_*>``
   placeholders are intentional string literals).

4. **The fixed-width single-record sample produces exactly 3 documented
   primary violations.**  The 80-char, 10-row worked example deliberately
   seeds three defects: a non-numeric value in a ``9(10)`` BALANCE field
   on row 8 (``FW_FMT_001``), an out-of-allowlist ACCT_STATUS on row 9
   (``FW_VAL_001``), and a truncated 75-char line on row 10
   (``FW_LEN_001``).  This test pins those three codes specifically so
   that a regression in the strict fixed-width validator can't silently
   drop a violation type.

The template-level tests run entirely against ``SourceConfig.model_validate``
(no filesystem I/O for the *referenced* paths -- only the YAML itself is
read).  The CSV round-trip test bypasses the file format detector (which
currently routes ``.csv`` to the pipe-delimited parser) and exercises
:class:`src.comparators.file_comparator.FileComparator` directly against
DataFrames built with ``pandas.read_csv``.  The fixed-width sample test
drives :class:`src.parsers.enhanced_validator.EnhancedFileValidator`
through :class:`src.parsers.fixed_width_parser.FixedWidthParser` -- the
same path ``valdo validate`` follows for fixed-width inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from src.comparators.file_comparator import FileComparator
from src.parsers.enhanced_validator import EnhancedFileValidator
from src.parsers.fixed_width_parser import FixedWidthParser
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

# Fixed-width single-record template (S6-3, #374)
FW_SINGLE_TEMPLATE = TEMPLATES_DIR / "fixed_width_single_record.yml"
FW_SINGLE_SAMPLE_DIR = TEMPLATES_DIR / "fixed_width_single_record_sample"
FW_SINGLE_SAMPLE_INPUT = FW_SINGLE_SAMPLE_DIR / "input.txt"
FW_SINGLE_SAMPLE_MAPPING = FW_SINGLE_SAMPLE_DIR / "mapping.json"
FW_SINGLE_SAMPLE_EXPECTED = FW_SINGLE_SAMPLE_DIR / "expected_report.json"


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


# ---------------------------------------------------------------------------
# S6-3 (#374) -- Fixed-width single-record template + sample
# ---------------------------------------------------------------------------


def test_fw_single_template_validates() -> None:
    """``fixed_width_single_record.yml`` loads cleanly through ``SourceConfig``.

    Mirrors the CSV-template contract: the committed YAML carries
    ``<FILL_IN_*>`` placeholders as string literals so the template
    validates as-shipped, and BAs see schema errors immediately if they
    misspell a key while replacing the placeholders.

    Asserts the required identity fields are preserved, the single
    declared input file lands in ``cfg.input_files``, and the strictness
    knobs (``strict_fixed_width``, ``strict_level``) survive as extras
    on the model (SourceConfig sets ``extra="allow"``).
    """
    assert FW_SINGLE_TEMPLATE.exists(), (
        f"Fixed-width single-record template missing at {FW_SINGLE_TEMPLATE}"
    )

    with FW_SINGLE_TEMPLATE.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    cfg = SourceConfig.model_validate(raw)

    # Required identity fields survive validation.
    assert cfg.source == "<FILL_IN_SOURCE_NAME>"
    assert cfg.schema_version == 1

    # One declared input file with the documented placeholder values.
    assert len(cfg.input_files) == 1
    entry = cfg.input_files[0]
    assert entry.file_type == "<FILL_IN_FILE_TYPE>"
    assert entry.glob == "<FILL_IN_GLOB>"
    assert entry.mapping == "<FILL_IN_PATH_TO_MAPPING_JSON>"
    assert entry.target_staging_table == ""

    # Strictness knobs preserved as extras for the CLI driver.
    extras = cfg.model_dump()
    assert extras.get("strict_fixed_width") is True
    assert extras.get("strict_level") == "all"


def test_fw_single_sample_finds_3_violations() -> None:
    """The fixed-width sample produces exactly the 3 documented violations.

    Drives :class:`EnhancedFileValidator` through :class:`FixedWidthParser`
    against the worked example -- the same code path ``valdo validate``
    follows for fixed-width inputs.

    The 80-char, 10-row ``input.txt`` is seeded with exactly three primary
    defects (rows 1-7 are clean):

    * **Row 8** -- BALANCE = ``ABCD123456`` violates ``format: "9(10)"``
      (``FW_FMT_001``).
    * **Row 9** -- ACCT_STATUS = ``XX`` violates
      ``valid_values=['AC','CL','SU']`` (``FW_VAL_001``).
    * **Row 10** -- line is 75 chars long; mapping expects 80
      (``FW_LEN_001``).

    The strict-validation engine also emits derivative diagnostics
    (alignment rollups, truncation impact messages); this test pins the
    three primary codes specifically so that a regression in any single
    check is caught without coupling to the exact number of derivative
    messages.
    """
    assert FW_SINGLE_SAMPLE_INPUT.exists(), (
        f"Fixed-width sample input missing at {FW_SINGLE_SAMPLE_INPUT}"
    )
    assert FW_SINGLE_SAMPLE_MAPPING.exists(), (
        f"Fixed-width sample mapping missing at {FW_SINGLE_SAMPLE_MAPPING}"
    )
    assert FW_SINGLE_SAMPLE_EXPECTED.exists(), (
        f"Fixed-width sample expected_report.json missing at {FW_SINGLE_SAMPLE_EXPECTED}"
    )

    # Load mapping and build the same field_specs the validate command builds.
    with FW_SINGLE_SAMPLE_MAPPING.open("r", encoding="utf-8") as fh:
        mapping = json.load(fh)
    mapping["file_path"] = str(FW_SINGLE_SAMPLE_MAPPING)

    field_specs = [
        (f["name"], f["position"] - 1, f["position"] - 1 + f["length"])
        for f in mapping["fields"]
    ]

    parser = FixedWidthParser(str(FW_SINGLE_SAMPLE_INPUT), field_specs)
    validator = EnhancedFileValidator(parser, mapping, None)
    result = validator.validate(detailed=False)

    assert result["valid"] is False, "Sample is seeded with 3 defects; must fail"

    errors = result["errors"]

    # Pin the three primary violations by (row, field, code).
    fmt_errors = [
        e for e in errors
        if e.get("code") == "FW_FMT_001"
        and e.get("row") == 8
        and e.get("field") == "BALANCE"
    ]
    val_errors = [
        e for e in errors
        if e.get("code") == "FW_VAL_001"
        and e.get("row") == 9
        and e.get("field") == "ACCT_STATUS"
    ]
    # FW_LEN_001 is emitted twice -- once as the per-row error (row 10) and
    # once as a rollup summary (row=None). The per-row entry is the primary.
    len_errors = [
        e for e in errors
        if e.get("code") == "FW_LEN_001"
        and e.get("row") == 10
    ]

    assert len(fmt_errors) == 1, (
        f"Expected exactly 1 FW_FMT_001 on row 8 (BALANCE); got {len(fmt_errors)}. "
        f"All errors: {[(e.get('row'), e.get('field'), e.get('code')) for e in errors]}"
    )
    assert len(val_errors) == 1, (
        f"Expected exactly 1 FW_VAL_001 on row 9 (ACCT_STATUS); got {len(val_errors)}. "
        f"All errors: {[(e.get('row'), e.get('field'), e.get('code')) for e in errors]}"
    )
    assert len(len_errors) == 1, (
        f"Expected exactly 1 FW_LEN_001 on row 10; got {len(len_errors)}. "
        f"All errors: {[(e.get('row'), e.get('field'), e.get('code')) for e in errors]}"
    )

    # Total of exactly 3 primary violations across the three pinned codes.
    primary_count = len(fmt_errors) + len(val_errors) + len(len_errors)
    assert primary_count == 3, (
        f"Expected 3 primary violations (FW_FMT_001 + FW_VAL_001 + FW_LEN_001); "
        f"got {primary_count}"
    )

    # Cross-check the expected_report.json contract committed alongside.
    with FW_SINGLE_SAMPLE_EXPECTED.open("r", encoding="utf-8") as fh:
        expected = json.load(fh)
    assert expected["summary"]["primary_violation_count"] == 3
    expected_codes = {p["code"] for p in expected["expected_primary_violations"]}
    assert expected_codes == {"FW_FMT_001", "FW_VAL_001", "FW_LEN_001"}
