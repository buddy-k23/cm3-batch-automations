"""End-to-end validation of the JSON (NDJSON) worked sample (S19-2, #395).

Proves the committed sample under
``templates/etl/json_single_record_sample/`` genuinely validates through the
real ``run_validate_service`` path (FormatDetector -> JsonParser ->
EnhancedFileValidator + the rule engine), and that the seeded defects surface
on the expected rows. Also guards the two integration fixes this story relies
on: the schema validator expecting ``<name>_count`` for array json_path
fields, and ``validate_nested_required`` flagging an absent path that
DataFrame construction coerces from ``pd.NA`` to ``np.nan``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "templates"
    / "etl"
    / "json_single_record_sample"
)


@pytest.fixture(autouse=True)
def _signing_key(monkeypatch):
    monkeypatch.setenv("VALDO_SESSION_SIGNING_KEY", "test-only-key")


def _run():
    from src.services.validate_service import run_validate_service

    return run_validate_service(
        file=str(_SAMPLE / "input.ndjson"),
        mapping=str(_SAMPLE / "mapping.json"),
        rules=str(_SAMPLE / "rules.json"),
        detailed=True,
        strict_level="all",
    )


def test_sample_files_present():
    for name in ("input.ndjson", "mapping.json", "rules.json", "expected_report.json"):
        assert (_SAMPLE / name).is_file(), f"missing sample file: {name}"


def test_sample_validates_end_to_end_with_seeded_defects():
    """The sample runs through the real engine and flags exactly 4 errors."""
    res = _run()
    assert res["total_rows"] == 10
    assert res["valid"] is False
    assert res["error_count"] == 4

    by_rule = {
        (e.get("rule_id"), e.get("field")): e for e in res["errors"]
    }
    # CUSTOMER_ID absent on line 9 (nested_required); STATUS 'FROZEN' line 8;
    # AGE 'NaN' line 10; empty transactions array line 6 (json_array_length).
    assert ("R001", "CUSTOMER_ID") in by_rule
    assert ("R002", "STATUS") in by_rule
    assert ("R003", "AGE") in by_rule
    assert ("R004", "TXN_COUNT_count") in by_rule
    rows = {e.get("row") or e.get("row_number") for e in res["errors"]}
    assert rows == {6, 8, 9, 10}


def test_sample_matches_committed_expected_report():
    """Re-running matches the committed expected_report.json (minus volatiles)."""
    res = _run()
    expected = json.loads((_SAMPLE / "expected_report.json").read_text())
    # Compare the stable, load-bearing fields (timestamps/metadata are volatile).
    for key in ("valid", "total_rows", "error_count", "warning_count"):
        assert res[key] == expected[key], f"{key} drifted from expected_report.json"


def test_no_spurious_array_count_schema_warning():
    """The array field's _count column is expected by schema (no missing/unexpected)."""
    res = _run()
    schema_msgs = [
        i.get("message", "")
        for i in res.get("errors", []) + res.get("warnings", [])
        if "TXN_COUNT" in str(i.get("field", "")) and "schema" == i.get("category")
    ]
    assert not schema_msgs, f"unexpected schema noise on the array field: {schema_msgs}"
