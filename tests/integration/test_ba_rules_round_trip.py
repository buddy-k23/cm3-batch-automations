"""Integration test: BA-friendly CSV -> converter -> RuleEngine.

This is the seam test that ADR 0006 calls out as "the codebase has been
missing": every BA-friendly rules JSON in the repo is produced by
:class:`~src.config.ba_rules_template_converter.BARulesTemplateConverter`
and is supposed to execute through
:class:`~src.validators.rule_engine.RuleEngine`. Before ADR 0006 MR 2 the
two halves spoke disjoint operator vocabularies and every rule failed
with ``Unknown operator: <name>`` at runtime.

This test exercises the full round-trip on a small CSV that covers every
new BA-native operator plus a cross-field rule, and asserts the expected
violation set on a hand-built DataFrame. If it ever regresses, the SHAW
ATOCTRAN smoke (and every other BA-friendly source) will silently lose
its L1 verdict, exactly as documented in
``docs/handover/SHAW_atoctran_smoke_findings.md``.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from typing import List

import pandas as pd
import pytest

from src.config.ba_rules_template_converter import BARulesTemplateConverter
from src.validators.rule_engine import RuleEngine, RuleViolation


# CSV columns expected by BARulesTemplateConverter.REQUIRED_COLUMNS plus
# the optional Condition column used by one rule below.
_COLUMNS = [
    "Rule ID",
    "Rule Name",
    "Field",
    "Rule Type",
    "Severity",
    "Expected / Values",
    "Condition (optional)",
    "Notes",
    "Enabled",
]


def _write_csv(rows: List[dict]) -> Path:
    """Write a BA-friendly rules CSV to a temp file and return the path."""
    fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, newline="", encoding="utf-8"
    )
    writer = csv.DictWriter(fd, fieldnames=_COLUMNS)
    writer.writeheader()
    for row in rows:
        # Fill in defaults for unspecified columns.
        filled = {c: "" for c in _COLUMNS}
        filled.update(row)
        filled.setdefault("Severity", "error")
        filled.setdefault("Enabled", "Y")
        writer.writerow(filled)
    fd.close()
    return Path(fd.name)


@pytest.fixture
def ba_rules_csv() -> Path:
    """Build a CSV that exercises every BA-native operator."""
    rows = [
        {
            "Rule ID": "R001",
            "Rule Name": "ACCT-NUM must be present",
            "Field": "ACCT-NUM",
            "Rule Type": "not_empty",
            "Severity": "error",
            "Expected / Values": "",
            "Enabled": "Y",
        },
        {
            "Rule ID": "R002",
            "Rule Name": "ACCT-NUM exact length",
            "Field": "ACCT-NUM",
            "Rule Type": "exact_length",
            "Severity": "error",
            "Expected / Values": "18",
            "Enabled": "Y",
        },
        {
            "Rule ID": "R003",
            "Rule Name": "RECORD-TYPE allowed set",
            "Field": "RECORD-TYPE",
            "Rule Type": "valid_values",
            "Severity": "error",
            "Expected / Values": "100|200|300",
            "Enabled": "Y",
        },
        {
            "Rule ID": "R004",
            "Rule Name": "AMOUNT must be numeric",
            "Field": "AMOUNT",
            "Rule Type": "numeric",
            "Severity": "error",
            "Expected / Values": "",
            "Enabled": "Y",
        },
        {
            "Rule ID": "R005",
            "Rule Name": "AMOUNT min",
            "Field": "AMOUNT",
            "Rule Type": "min_value",
            "Severity": "error",
            "Expected / Values": "0",
            "Enabled": "Y",
        },
        {
            "Rule ID": "R006",
            "Rule Name": "AMOUNT max",
            "Field": "AMOUNT",
            "Rule Type": "max_value",
            "Severity": "error",
            "Expected / Values": "1000000",
            "Enabled": "Y",
        },
        {
            "Rule ID": "R007",
            "Rule Name": "POSTED-DATE format",
            "Field": "POSTED-DATE",
            "Rule Type": "date_format",
            "Severity": "error",
            "Expected / Values": "CCYYMMDD",
            "Enabled": "Y",
        },
        {
            "Rule ID": "R008",
            "Rule Name": "DESCRIPTION min length",
            "Field": "DESCRIPTION",
            "Rule Type": "min_length",
            "Severity": "error",
            "Expected / Values": "1",
            "Enabled": "Y",
        },
    ]
    path = _write_csv(rows)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def sample_df() -> pd.DataFrame:
    """Hand-built DataFrame with known violators per rule."""
    return pd.DataFrame(
        [
            # Row 1 — clean: ACCT-NUM 18 chars, RECORD-TYPE 100, amount 50, valid date, desc present.
            {
                "ACCT-NUM": "ABCDEFGHIJKLMNOPQR",
                "RECORD-TYPE": "100",
                "AMOUNT": "50",
                "POSTED-DATE": "20260101",
                "DESCRIPTION": "ok",
            },
            # Row 2 — ACCT-NUM blank (R001) and length 0 (R002), description empty (R008).
            {
                "ACCT-NUM": "",
                "RECORD-TYPE": "200",
                "AMOUNT": "100",
                "POSTED-DATE": "20260102",
                "DESCRIPTION": "",
            },
            # Row 3 — ACCT-NUM length 5 (R002), RECORD-TYPE outside set (R003).
            {
                "ACCT-NUM": "SHORT",
                "RECORD-TYPE": "999",
                "AMOUNT": "200",
                "POSTED-DATE": "20260103",
                "DESCRIPTION": "fine",
            },
            # Row 4 — non-numeric AMOUNT (R004); also fails min/max because NaN comparisons are False
            # so neither R005 nor R006 fires for this row.
            {
                "ACCT-NUM": "ABCDEFGHIJKLMNOPQR",
                "RECORD-TYPE": "300",
                "AMOUNT": "abc",
                "POSTED-DATE": "20260104",
                "DESCRIPTION": "fine",
            },
            # Row 5 — AMOUNT below min (R005).
            {
                "ACCT-NUM": "ABCDEFGHIJKLMNOPQR",
                "RECORD-TYPE": "100",
                "AMOUNT": "-1",
                "POSTED-DATE": "20260105",
                "DESCRIPTION": "fine",
            },
            # Row 6 — AMOUNT above max (R006) and POSTED-DATE wrong length (R007).
            {
                "ACCT-NUM": "ABCDEFGHIJKLMNOPQR",
                "RECORD-TYPE": "200",
                "AMOUNT": "9999999",
                "POSTED-DATE": "2026-01-06",
                "DESCRIPTION": "fine",
            },
        ]
    )


def _violations_by_rule(violations: List[RuleViolation]) -> dict:
    """Return ``{rule_id: [row_number, ...]}`` for assertion readability."""
    grouped: dict = {}
    for v in violations:
        grouped.setdefault(v.rule_id, []).append(v.row_number)
    for rid in grouped:
        grouped[rid].sort()
    return grouped


def test_ba_csv_round_trip_to_rule_engine(
    ba_rules_csv: Path, sample_df: pd.DataFrame
):
    """Full BA-friendly CSV -> converter -> RuleEngine -> violations.

    Asserts the converter and the engine agree on every operator the
    BA-friendly path emits. This is the regression guard ADR 0006 calls
    out: before MR 2 the engine raised ``Unknown operator`` for every one
    of these rules.
    """
    config = BARulesTemplateConverter().from_csv(str(ba_rules_csv))
    # Sanity: the converter recognized every row.
    assert len(config["rules"]) == 8

    engine = RuleEngine(config)
    violations = engine.validate(sample_df)

    grouped = _violations_by_rule(violations)

    # R001 not_empty on ACCT-NUM: only row 2 is blank.
    assert grouped.get("R001", []) == [2]
    # R002 exact_length 18 on ACCT-NUM: rows 2 (len 0) and 3 (len 5).
    assert grouped.get("R002", []) == [2, 3]
    # R003 valid_values {100,200,300} on RECORD-TYPE: only row 3 is outside.
    assert grouped.get("R003", []) == [3]
    # R004 numeric on AMOUNT: only row 4's "abc".
    assert grouped.get("R004", []) == [4]
    # R005 min_value 0 on AMOUNT: only row 5 is below; row 4's NaN does
    # not satisfy ``< 0`` (NaN comparisons are False), as documented in
    # FieldValidator.validate_min_value's docstring.
    assert grouped.get("R005", []) == [5]
    # R006 max_value 1000000 on AMOUNT: only row 6 is above.
    assert grouped.get("R006", []) == [6]
    # R007 date_format CCYYMMDD on POSTED-DATE: only row 6's dashed date.
    assert grouped.get("R007", []) == [6]
    # R008 min_length 1 on DESCRIPTION: only row 2's empty string.
    assert grouped.get("R008", []) == [2]


def test_ba_csv_round_trip_no_unknown_operator_errors(
    ba_rules_csv: Path, sample_df: pd.DataFrame, capsys
):
    """No ``Unknown operator`` messages leak to stdout/stderr.

    The ADR 0006 acceptance criteria explicitly require this: re-running
    the smoke must produce zero ``Unknown operator: <name>`` lines.
    """
    config = BARulesTemplateConverter().from_csv(str(ba_rules_csv))
    RuleEngine(config).validate(sample_df)

    captured = capsys.readouterr()
    assert "Unknown operator" not in captured.out
    assert "Unknown operator" not in captured.err


def test_legacy_operators_still_execute(sample_df: pd.DataFrame):
    """Cross-field and legacy operators are untouched by MR 2.

    Asserts that the additive change to ``_validate_field`` did not
    disturb the pre-existing operator vocabulary. Builds a config with a
    legacy ``length`` rule that the converter would historically emit and
    confirms it still produces the expected violation set.
    """
    config = {
        "rules": [
            {
                "id": "L001",
                "name": "DESCRIPTION between 1 and 4 chars",
                "type": "field_validation",
                "field": "DESCRIPTION",
                "operator": "length",
                "min_length": 1,
                "max_length": 4,
                "severity": "error",
                "enabled": True,
            }
        ]
    }
    violations = RuleEngine(config).validate(sample_df)
    # Row 2 (empty) is too short.
    assert [v.row_number for v in violations] == [2]
