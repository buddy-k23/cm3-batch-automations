"""Tests for CrossRowValidator — issue #149.

Covers all 6 cross-row check types:
  - unique
  - unique_composite
  - consistent
  - sequential
  - group_count
  - group_sum

Plus edge cases: empty DataFrame, single-row groups, missing columns, NaN values.
"""

import pandas as pd
import pytest

from src.validators.cross_row_validator import CrossRowValidator
from src.validators.rule_engine import RuleViolation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rule(check: str, **kwargs) -> dict:
    """Return a minimal cross_row rule dict for the given check type."""
    return {
        "id": "CR_TEST",
        "name": f"test_{check}",
        "type": "cross_row",
        "check": check,
        "severity": "error",
        **kwargs,
    }


# ---------------------------------------------------------------------------
# unique
# ---------------------------------------------------------------------------

class TestCheckUnique:
    """Tests for the 'unique' cross-row check."""

    def test_unique_passes_with_distinct_values(self):
        """No violations when all values in the field are distinct."""
        df = pd.DataFrame({"ACCOUNT": ["A001", "A002", "A003"]})
        rule = _make_rule("unique", field="ACCOUNT")
        validator = CrossRowValidator()
        violations = validator.validate(rule, df)
        assert violations == []

    def test_unique_fails_with_duplicate_values(self):
        """Each duplicated row produces a violation."""
        df = pd.DataFrame({"ACCOUNT": ["A001", "A002", "A001", "A003", "A002"]})
        rule = _make_rule("unique", field="ACCOUNT")
        validator = CrossRowValidator()
        violations = validator.validate(rule, df)
        # Rows 0, 2 share "A001"; rows 1, 4 share "A002" — all 4 are violations
        assert len(violations) == 4
        assert all(isinstance(v, RuleViolation) for v in violations)

    def test_unique_reports_correct_1indexed_row_numbers(self):
        """row_number in violations is 1-indexed (user-facing)."""
        df = pd.DataFrame({"ACCOUNT": ["A001", "A001"]})
        rule = _make_rule("unique", field="ACCOUNT")
        violations = CrossRowValidator().validate(rule, df)
        row_numbers = {v.row_number for v in violations}
        assert row_numbers == {1, 2}

    def test_unique_missing_column_returns_empty(self):
        """Gracefully returns no violations when field is absent."""
        df = pd.DataFrame({"OTHER": [1, 2, 3]})
        rule = _make_rule("unique", field="ACCOUNT")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_unique_empty_dataframe_returns_empty(self):
        """No violations on an empty DataFrame."""
        df = pd.DataFrame({"ACCOUNT": pd.Series([], dtype=str)})
        rule = _make_rule("unique", field="ACCOUNT")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_unique_nan_values_not_flagged_as_duplicates(self):
        """NaN values are not treated as duplicates of each other."""
        df = pd.DataFrame({"ACCOUNT": ["A001", None, None]})
        rule = _make_rule("unique", field="ACCOUNT")
        violations = CrossRowValidator().validate(rule, df)
        # Only "A001" is distinct; NaN rows should not be flagged
        assert len(violations) == 0


# ---------------------------------------------------------------------------
# unique_composite
# ---------------------------------------------------------------------------

class TestCheckUniqueComposite:
    """Tests for the 'unique_composite' cross-row check."""

    def test_unique_composite_passes_with_distinct_combinations(self):
        """No violations when every row has a unique column combination."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A002"],
            "ITEM":    ["1",    "2",    "1"],
        })
        rule = _make_rule("unique_composite", fields=["ACCOUNT", "ITEM"])
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_unique_composite_fails_with_duplicate_combination(self):
        """Duplicate row-combinations each produce a violation."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A001"],
            "ITEM":    ["1",    "1",    "2"],
        })
        rule = _make_rule("unique_composite", fields=["ACCOUNT", "ITEM"])
        violations = CrossRowValidator().validate(rule, df)
        # rows 0 and 1 share (A001, 1)
        assert len(violations) == 2

    def test_unique_composite_missing_column_returns_empty(self):
        """Returns no violations when any composite field is absent."""
        df = pd.DataFrame({"ACCOUNT": ["A001", "A001"]})
        rule = _make_rule("unique_composite", fields=["ACCOUNT", "MISSING"])
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_unique_composite_empty_dataframe_returns_empty(self):
        df = pd.DataFrame({"ACCOUNT": pd.Series([], dtype=str),
                           "ITEM": pd.Series([], dtype=str)})
        rule = _make_rule("unique_composite", fields=["ACCOUNT", "ITEM"])
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []


# ---------------------------------------------------------------------------
# consistent
# ---------------------------------------------------------------------------

class TestCheckConsistent:
    """Tests for the 'consistent' cross-row check."""

    def test_consistent_passes_when_target_uniform_per_key(self):
        """No violations when target_field has the same value within each key group."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A002", "A002"],
            "BANK":    ["BK1",  "BK1",  "BK2",  "BK2"],
        })
        rule = _make_rule("consistent", key_field="ACCOUNT", target_field="BANK")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_consistent_fails_when_target_differs_within_key(self):
        """Rows in a key group with conflicting target values produce violations."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A002"],
            "BANK":    ["BK1",  "BK2",  "BK3"],
        })
        rule = _make_rule("consistent", key_field="ACCOUNT", target_field="BANK")
        violations = CrossRowValidator().validate(rule, df)
        # Both rows 0 and 1 belong to A001 which is inconsistent
        assert len(violations) == 2

    def test_consistent_single_row_groups_always_pass(self):
        """Single-row groups are trivially consistent."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A002", "A003"],
            "BANK":    ["BK1",  "BK2",  "BK3"],
        })
        rule = _make_rule("consistent", key_field="ACCOUNT", target_field="BANK")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_consistent_missing_key_column_returns_empty(self):
        df = pd.DataFrame({"BANK": ["BK1", "BK2"]})
        rule = _make_rule("consistent", key_field="ACCOUNT", target_field="BANK")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_consistent_missing_target_column_returns_empty(self):
        df = pd.DataFrame({"ACCOUNT": ["A001", "A001"]})
        rule = _make_rule("consistent", key_field="ACCOUNT", target_field="BANK")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_consistent_empty_dataframe_returns_empty(self):
        df = pd.DataFrame({"ACCOUNT": pd.Series([], dtype=str),
                           "BANK": pd.Series([], dtype=str)})
        rule = _make_rule("consistent", key_field="ACCOUNT", target_field="BANK")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []


# ---------------------------------------------------------------------------
# sequential
# ---------------------------------------------------------------------------

class TestCheckSequential:
    """Tests for the 'sequential' cross-row check."""

    def test_sequential_passes_with_correct_sequence(self):
        """No violations when sequence_field is 1,2,3,... within each key group."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A001", "A002", "A002"],
            "ITEM":    [1,      2,      3,      1,      2],
        })
        rule = _make_rule("sequential", key_field="ACCOUNT", sequence_field="ITEM")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_sequential_fails_with_gap_in_sequence(self):
        """Gap in sequence (1, 3 — missing 2) with 3 rows produces violations."""
        # 3 rows in the group but values are {1, 3, 4} — missing 2, not 1..3
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A001"],
            "ITEM":    [1,      3,      4],
        })
        rule = _make_rule("sequential", key_field="ACCOUNT", sequence_field="ITEM")
        violations = CrossRowValidator().validate(rule, df)
        assert len(violations) > 0

    def test_sequential_fails_when_sequence_does_not_start_at_one(self):
        """Sequence starting at 2 instead of 1 produces violations."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001"],
            "ITEM":    [2,      3],
        })
        rule = _make_rule("sequential", key_field="ACCOUNT", sequence_field="ITEM")
        violations = CrossRowValidator().validate(rule, df)
        assert len(violations) > 0

    def test_sequential_passes_with_out_of_order_rows(self):
        """Rows need not be sorted; only the set of values matters (1..N)."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A001"],
            "ITEM":    [3,      1,      2],
        })
        rule = _make_rule("sequential", key_field="ACCOUNT", sequence_field="ITEM")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_sequential_single_row_group_passes(self):
        """A group with a single row should have sequence value of 1."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001"],
            "ITEM":    [1],
        })
        rule = _make_rule("sequential", key_field="ACCOUNT", sequence_field="ITEM")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_sequential_missing_key_column_returns_empty(self):
        df = pd.DataFrame({"ITEM": [1, 2, 3]})
        rule = _make_rule("sequential", key_field="ACCOUNT", sequence_field="ITEM")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_sequential_empty_dataframe_returns_empty(self):
        df = pd.DataFrame({"ACCOUNT": pd.Series([], dtype=str),
                           "ITEM": pd.Series([], dtype=int)})
        rule = _make_rule("sequential", key_field="ACCOUNT", sequence_field="ITEM")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []


class TestCheckSequentialStartStep:
    """Tests for the parameterised 'sequential' run (start/step)."""

    def test_descending_run_from_998_passes(self):
        """start=998, step=-1 requires 998, 997, 996, ... per group."""
        df = pd.DataFrame({
            "ACCT": ["A", "A", "A", "B"],
            "REF":  [998, 997, 996, 998],
        })
        rule = _make_rule(
            "sequential", key_field="ACCT", sequence_field="REF",
            start=998, step=-1,
        )
        assert CrossRowValidator().validate(rule, df) == []

    def test_descending_run_single_contact_must_be_998(self):
        """A single 32005 row whose ordinal is not 998 fails (the 994 defect)."""
        df = pd.DataFrame({"ACCT": ["A"], "REF": [994]})
        rule = _make_rule(
            "sequential", key_field="ACCT", sequence_field="REF",
            start=998, step=-1,
        )
        violations = CrossRowValidator().validate(rule, df)
        assert len(violations) == 1

    def test_descending_run_with_gap_fails(self):
        """998, 996 (missing 997) for a 2-row group fails."""
        df = pd.DataFrame({"ACCT": ["A", "A"], "REF": [998, 996]})
        rule = _make_rule(
            "sequential", key_field="ACCT", sequence_field="REF",
            start=998, step=-1,
        )
        assert len(CrossRowValidator().validate(rule, df)) == 2

    def test_descending_run_out_of_order_passes(self):
        """Order-independent: the multiset {998,997,996} passes regardless of order."""
        df = pd.DataFrame({"ACCT": ["A", "A", "A"], "REF": [996, 998, 997]})
        rule = _make_rule(
            "sequential", key_field="ACCT", sequence_field="REF",
            start=998, step=-1,
        )
        assert CrossRowValidator().validate(rule, df) == []

    def test_ascending_run_from_zero(self):
        """start=0, step=1 requires 0,1,2,... per group."""
        df = pd.DataFrame({"ACCT": ["A", "A", "A"], "REF": [0, 1, 2]})
        rule = _make_rule(
            "sequential", key_field="ACCT", sequence_field="REF",
            start=0, step=1,
        )
        assert CrossRowValidator().validate(rule, df) == []

    def test_default_start_step_is_backward_compatible(self):
        """Omitting start/step preserves the classic 1,2,3,... behaviour."""
        df = pd.DataFrame({"ACCT": ["A", "A"], "REF": [1, 2]})
        rule = _make_rule("sequential", key_field="ACCT", sequence_field="REF")
        assert CrossRowValidator().validate(rule, df) == []

    def test_zero_step_raises(self):
        df = pd.DataFrame({"ACCT": ["A", "A"], "REF": [1, 1]})
        rule = _make_rule(
            "sequential", key_field="ACCT", sequence_field="REF", step=0,
        )
        with pytest.raises(ValueError):
            CrossRowValidator().validate(rule, df)

    def test_non_integer_start_raises(self):
        df = pd.DataFrame({"ACCT": ["A"], "REF": [1]})
        rule = _make_rule(
            "sequential", key_field="ACCT", sequence_field="REF", start="abc",
        )
        with pytest.raises(ValueError):
            CrossRowValidator().validate(rule, df)


# ---------------------------------------------------------------------------
# group_count
# ---------------------------------------------------------------------------

class TestCheckGroupCount:
    """Tests for the 'group_count' cross-row check."""

    def test_group_count_passes_when_count_matches(self):
        """No violations when actual row count equals the declared count field."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A001"],
            "TXN_CNT": [3,      3,      3],
        })
        rule = _make_rule("group_count", key_field="ACCOUNT", count_field="TXN_CNT")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_group_count_fails_when_count_mismatches(self):
        """Violation produced when declared count differs from actual row count."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A001"],
            "TXN_CNT": [5,      5,      5],
        })
        rule = _make_rule("group_count", key_field="ACCOUNT", count_field="TXN_CNT")
        violations = CrossRowValidator().validate(rule, df)
        assert len(violations) > 0

    def test_group_count_multiple_groups_partial_mismatch(self):
        """Only the mismatched group generates violations."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A002", "A002"],
            "TXN_CNT": [2,      2,      3,      3],
        })
        rule = _make_rule("group_count", key_field="ACCOUNT", count_field="TXN_CNT")
        violations = CrossRowValidator().validate(rule, df)
        # A001 has count=2 actual=2 (pass); A002 has count=3 actual=2 (fail)
        assert len(violations) == 2

    def test_group_count_missing_key_column_returns_empty(self):
        df = pd.DataFrame({"TXN_CNT": [2, 2]})
        rule = _make_rule("group_count", key_field="ACCOUNT", count_field="TXN_CNT")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_group_count_empty_dataframe_returns_empty(self):
        df = pd.DataFrame({"ACCOUNT": pd.Series([], dtype=str),
                           "TXN_CNT": pd.Series([], dtype=int)})
        rule = _make_rule("group_count", key_field="ACCOUNT", count_field="TXN_CNT")
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []


# ---------------------------------------------------------------------------
# group_sum
# ---------------------------------------------------------------------------

class TestCheckGroupSum:
    """Tests for the 'group_sum' cross-row check."""

    def test_group_sum_passes_within_bounds(self):
        """No violations when group sum is within [min_value, max_value]."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A002"],
            "AMOUNT":  [100.0,  200.0,  50.0],
        })
        rule = _make_rule(
            "group_sum",
            key_field="ACCOUNT",
            sum_field="AMOUNT",
            min_value=0,
            max_value=500,
        )
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_group_sum_fails_when_sum_exceeds_max(self):
        """Violations produced for groups whose sum exceeds max_value."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A002"],
            "AMOUNT":  [400.0,  400.0,  50.0],  # A001 sum = 800 > 500
        })
        rule = _make_rule(
            "group_sum",
            key_field="ACCOUNT",
            sum_field="AMOUNT",
            min_value=0,
            max_value=500,
        )
        violations = CrossRowValidator().validate(rule, df)
        # Both A001 rows flagged; A002 is fine
        assert len(violations) == 2

    def test_group_sum_fails_when_sum_below_min(self):
        """Violations produced for groups whose sum is below min_value."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001"],
            "AMOUNT":  [10.0,   5.0],   # sum = 15 < 100
        })
        rule = _make_rule(
            "group_sum",
            key_field="ACCOUNT",
            sum_field="AMOUNT",
            min_value=100,
            max_value=9999,
        )
        violations = CrossRowValidator().validate(rule, df)
        assert len(violations) == 2

    def test_group_sum_no_max_value_only_min(self):
        """When max_value is absent, only min_value is enforced."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001"],
            "AMOUNT":  [50.0],   # sum = 50 >= 10 (ok)
        })
        rule = _make_rule(
            "group_sum",
            key_field="ACCOUNT",
            sum_field="AMOUNT",
            min_value=10,
        )
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_group_sum_missing_sum_column_returns_empty(self):
        df = pd.DataFrame({"ACCOUNT": ["A001", "A001"]})
        rule = _make_rule(
            "group_sum",
            key_field="ACCOUNT",
            sum_field="AMOUNT",
            min_value=0,
            max_value=999,
        )
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []

    def test_group_sum_empty_dataframe_returns_empty(self):
        df = pd.DataFrame({"ACCOUNT": pd.Series([], dtype=str),
                           "AMOUNT": pd.Series([], dtype=float)})
        rule = _make_rule(
            "group_sum",
            key_field="ACCOUNT",
            sum_field="AMOUNT",
            min_value=0,
            max_value=999,
        )
        violations = CrossRowValidator().validate(rule, df)
        assert violations == []


# ---------------------------------------------------------------------------
# Unknown check type
# ---------------------------------------------------------------------------

class TestUnknownCheckType:
    """validate() must raise ValueError for an unrecognised check type."""

    def test_unknown_check_raises_value_error(self):
        df = pd.DataFrame({"X": [1, 2]})
        rule = _make_rule("bogus_check", field="X")
        with pytest.raises(ValueError, match="Unknown cross_row check"):
            CrossRowValidator().validate(rule, df)


# ---------------------------------------------------------------------------
# RuleEngine integration — cross_row dispatch
# ---------------------------------------------------------------------------

class TestRuleEngineDispatchesCrossRow:
    """RuleEngine must route cross_row rules to CrossRowValidator."""

    def test_rule_engine_unique_via_cross_row_type(self):
        """A cross_row rule with check=unique is dispatched and produces violations."""
        df = pd.DataFrame({"ACCT": ["A001", "A001", "A002"]})
        config = {
            "rules": [
                {
                    "id": "CR1",
                    "name": "Account must be unique",
                    "type": "cross_row",
                    "check": "unique",
                    "field": "ACCT",
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        from src.validators.rule_engine import RuleEngine
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 2  # rows 0 and 1 share A001

    def test_rule_engine_cross_row_consistent_no_violations(self):
        """cross_row consistent rule produces zero violations on clean data."""
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A002"],
            "BANK":    ["BK1",  "BK1",  "BK2"],
        })
        config = {
            "rules": [
                {
                    "id": "CR2",
                    "name": "Bank consistent per account",
                    "type": "cross_row",
                    "check": "consistent",
                    "key_field": "ACCOUNT",
                    "target_field": "BANK",
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        from src.validators.rule_engine import RuleEngine
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert violations == []

    def test_rule_engine_when_condition_applied_before_cross_row(self):
        """The `when` filter is applied before passing rows to CrossRowValidator."""
        # Only rows with STATUS=ACTIVE go to the unique check.
        # A001 appears twice with STATUS=ACTIVE → violation.
        # A003 appears twice with STATUS=INACTIVE → filtered out, no violation.
        df = pd.DataFrame({
            "ACCOUNT": ["A001", "A001", "A003", "A003"],
            "STATUS":  ["ACTIVE", "ACTIVE", "INACTIVE", "INACTIVE"],
        })
        config = {
            "rules": [
                {
                    "id": "CR3",
                    "name": "Active accounts must be unique",
                    "type": "cross_row",
                    "check": "unique",
                    "field": "ACCOUNT",
                    "when": "STATUS = ACTIVE",
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        from src.validators.rule_engine import RuleEngine
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 2
        # All violations must reference ACTIVE rows (1-indexed rows 1 and 2)
        row_numbers = {v.row_number for v in violations}
        assert row_numbers == {1, 2}

    def test_rule_engine_existing_field_validation_unchanged(self):
        """Adding cross_row support must not break existing field_validation rules."""
        df = pd.DataFrame({"STATUS": ["ACTIVE", "INVALID"]})
        config = {
            "rules": [
                {
                    "id": "R1",
                    "name": "Status must be ACTIVE",
                    "type": "field_validation",
                    "field": "STATUS",
                    "operator": "in",
                    "values": ["ACTIVE"],
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        from src.validators.rule_engine import RuleEngine
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 1
        assert violations[0].row_number == 2

    def test_rule_engine_issue_code_uses_cross_suffix(self):
        """cross_row violations carry an issue_code ending in _CROSS."""
        df = pd.DataFrame({"ACCT": ["DUP", "DUP"]})
        config = {
            "rules": [
                {
                    "id": "CR99",
                    "name": "Unique account",
                    "type": "cross_row",
                    "check": "unique",
                    "field": "ACCT",
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        from src.validators.rule_engine import RuleEngine
        violations = RuleEngine(config).validate(df)
        assert all(v.issue_code.endswith("_CROSS") for v in violations)
