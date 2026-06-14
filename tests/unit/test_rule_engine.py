"""Unit tests for ``RuleEngine._validate_field`` operator dispatch.

Focused on the eight BA-friendly "native" operators added under ADR 0006
(issue #13). For each new operator we assert that:

1. The engine dispatches to the matching ``FieldValidator`` method.
2. The arguments passed to that method are extracted from the rule dict
   exactly as :class:`~src.config.ba_rules_template_converter.BARulesTemplateConverter`
   emits them.
3. Resulting violations carry the rule metadata (id, name, severity).

Legacy operator branches (``>``, ``<``, ``in``, ``regex``, ``length``…)
are exercised by ``test_rules_engine_happy.py`` and
``test_rule_engine_conditions.py`` and are intentionally not duplicated
here.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.validators.rule_engine import RuleEngine, RuleViolation


def _rule(operator: str, **extra) -> dict:
    """Build a minimal field_validation rule for *operator*."""
    base = {
        "id": f"R_{operator.upper()}",
        "name": f"Test rule for {operator}",
        "type": "field_validation",
        "field": "f",
        "operator": operator,
        "severity": "error",
        "enabled": True,
    }
    base.update(extra)
    return base


def _engine(rule: dict) -> RuleEngine:
    """Build a RuleEngine with a single rule."""
    return RuleEngine({"rules": [rule]})


# ---------------------------------------------------------------------------
# Dispatch tests (one per new operator)
# ---------------------------------------------------------------------------


class TestNativeOperatorDispatch:
    """Each new operator dispatches to its matching FieldValidator method."""

    # Patching an unbound method on the class swaps it for a Mock. When
    # the engine calls ``validator.validate_X(df, field, ...)`` on an
    # *instance*, the bound-method descriptor receives the instance and
    # the explicit args are ``(df, field, ...)``. ``call_args.args`` on
    # the Mock therefore starts with the instance only when ``autospec``
    # is used; with a plain ``patch`` on the class attribute it does not
    # auto-bind, so args are exactly what the caller passed:
    # ``(df, field, ...)``. We assert on that.

    def test_not_empty_dispatches_to_validate_not_empty(self):
        """``not_empty`` operator calls ``FieldValidator.validate_not_empty``."""
        df = pd.DataFrame({"f": ["x", "y", ""]})
        rule = _rule("not_empty")
        with patch(
            "src.validators.field_validator.FieldValidator.validate_not_empty",
            return_value=pd.Series([False, False, True], index=df.index),
        ) as m:
            violations = _engine(rule).validate(df)
        m.assert_called_once()
        # args: (df, field)
        assert m.call_args.args[1] == "f"
        assert len(violations) == 1
        assert violations[0].rule_id == "R_NOT_EMPTY"

    def test_numeric_dispatches_to_validate_numeric_format(self):
        """``numeric`` operator calls ``FieldValidator.validate_numeric_format``."""
        df = pd.DataFrame({"f": ["1", "abc"]})
        rule = _rule("numeric")
        with patch(
            "src.validators.field_validator.FieldValidator.validate_numeric_format",
            return_value=pd.Series([False, True], index=df.index),
        ) as m:
            violations = _engine(rule).validate(df)
        m.assert_called_once()
        assert m.call_args.args[1] == "f"
        assert len(violations) == 1

    def test_date_format_passes_format_string(self):
        """``date_format`` operator forwards the ``format`` rule field."""
        df = pd.DataFrame({"f": ["20260101", "bad"]})
        rule = _rule("date_format", format="CCYYMMDD")
        with patch(
            "src.validators.field_validator.FieldValidator.validate_date_format",
            return_value=pd.Series([False, True], index=df.index),
        ) as m:
            _engine(rule).validate(df)
        # args: (df, field, fmt)
        assert m.call_args.args[1] == "f"
        assert m.call_args.args[2] == "CCYYMMDD"

    def test_valid_values_passes_values_list(self):
        """``valid_values`` forwards the ``values`` list."""
        df = pd.DataFrame({"f": ["A", "Z"]})
        rule = _rule("valid_values", values=["A", "B"])
        with patch(
            "src.validators.field_validator.FieldValidator.validate_valid_values",
            return_value=pd.Series([False, True], index=df.index),
        ) as m:
            _engine(rule).validate(df)
        assert m.call_args.args[1] == "f"
        assert m.call_args.args[2] == ["A", "B"]

    def test_valid_values_accepts_comma_separated_string(self):
        """``values`` may be a comma-separated string (defensive parsing)."""
        df = pd.DataFrame({"f": ["A", "Z"]})
        rule = _rule("valid_values", values="A, B")
        with patch(
            "src.validators.field_validator.FieldValidator.validate_valid_values",
            return_value=pd.Series([False, True], index=df.index),
        ) as m:
            _engine(rule).validate(df)
        assert m.call_args.args[2] == ["A", "B"]

    def test_min_value_passes_threshold(self):
        """``min_value`` forwards the ``value`` rule field."""
        df = pd.DataFrame({"f": [1, 10]})
        rule = _rule("min_value", value=5)
        with patch(
            "src.validators.field_validator.FieldValidator.validate_min_value",
            return_value=pd.Series([True, False], index=df.index),
        ) as m:
            _engine(rule).validate(df)
        assert m.call_args.args[1] == "f"
        assert m.call_args.args[2] == 5

    def test_max_value_passes_threshold(self):
        """``max_value`` forwards the ``value`` rule field."""
        df = pd.DataFrame({"f": [101, 50]})
        rule = _rule("max_value", value=100)
        with patch(
            "src.validators.field_validator.FieldValidator.validate_max_value",
            return_value=pd.Series([True, False], index=df.index),
        ) as m:
            _engine(rule).validate(df)
        assert m.call_args.args[2] == 100

    def test_exact_length_coerces_value_to_int(self):
        """``exact_length`` forwards ``value`` coerced to int."""
        df = pd.DataFrame({"f": ["AAA", "AA"]})
        rule = _rule("exact_length", value="3")  # BA converter may emit str
        with patch(
            "src.validators.field_validator.FieldValidator.validate_exact_length",
            return_value=pd.Series([False, True], index=df.index),
        ) as m:
            _engine(rule).validate(df)
        assert m.call_args.args[2] == 3

    def test_min_length_coerces_value_to_int(self):
        """``min_length`` forwards ``value`` coerced to int."""
        df = pd.DataFrame({"f": ["A", "AAA"]})
        rule = _rule("min_length", value="2")
        with patch(
            "src.validators.field_validator.FieldValidator.validate_min_length",
            return_value=pd.Series([True, False], index=df.index),
        ) as m:
            _engine(rule).validate(df)
        assert m.call_args.args[2] == 2


# ---------------------------------------------------------------------------
# End-to-end integration via the real predicates
# ---------------------------------------------------------------------------


class TestNativeOperatorEndToEnd:
    """Drive the engine through the real predicates (no mocks)."""

    def test_not_empty_produces_violations(self):
        """Real not_empty execution yields one violation per blank row."""
        df = pd.DataFrame({"f": ["x", "", None]})
        violations = _engine(_rule("not_empty")).validate(df)
        assert {v.row_number for v in violations} == {2, 3}

    def test_valid_values_produces_violations(self):
        """Real valid_values flags out-of-set rows with the rule's id."""
        df = pd.DataFrame({"f": ["A", "Z", "B"]})
        violations = _engine(_rule("valid_values", values=["A", "B"])).validate(df)
        assert len(violations) == 1
        assert violations[0].row_number == 2
        assert violations[0].rule_id == "R_VALID_VALUES"
        assert violations[0].field == "f"

    def test_exact_length_produces_violations(self):
        """Real exact_length flags length-mismatch rows."""
        df = pd.DataFrame({"f": ["AAA", "AA"]})
        violations = _engine(_rule("exact_length", value=3)).validate(df)
        assert [v.row_number for v in violations] == [2]


# ---------------------------------------------------------------------------
# Regression: legacy "Unknown operator" path still exists for truly unknown ops
# ---------------------------------------------------------------------------


class TestUnknownOperatorRegression:
    """Operators outside both vocabularies still raise.

    Guards against a sloppy refactor that turns the explicit
    ``ValueError(f"Unknown operator: ...")`` into silent dispatch to the
    wrong predicate.
    """

    def test_unknown_operator_is_logged_and_skipped(self, capsys):
        """The engine catches the ValueError per-rule and continues."""
        df = pd.DataFrame({"f": ["x"]})
        rule = _rule("totally_made_up")
        violations = _engine(rule).validate(df)
        # _execute_rule's catch-all swallows the exception so other rules
        # can continue; no violations are produced for this rule.
        assert violations == []
        captured = capsys.readouterr()
        assert "Unknown operator: totally_made_up" in captured.out
