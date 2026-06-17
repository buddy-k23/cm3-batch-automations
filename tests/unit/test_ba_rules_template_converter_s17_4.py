"""Unit tests for BARulesTemplateConverter (S17-4, #432).

Covers each branch of _convert_row: required, allowed values, range, length,
regex, date format, the Valdo-native passthrough types, cross_field, and the
cross_row:* family, plus descriptive-text filtering and save().
"""

import json

import pandas as pd
import pytest

from src.config.ba_rules_template_converter import (
    BARulesTemplateConverter,
    _is_descriptive_text,
)


def _row(**overrides):
    base = {
        "Rule ID": "R1",
        "Rule Name": "Name",
        "Field": "acct",
        "Rule Type": "required",
        "Severity": "error",
        "Expected / Values": "",
        "Enabled": "Y",
    }
    base.update(overrides)
    return base


def _convert(tmp_path, rows):
    path = tmp_path / "rules.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return BARulesTemplateConverter(frozen_timestamp="GENERATED").from_csv(str(path))


class TestIsDescriptiveText:
    def test_phrase_detected(self):
        assert _is_descriptive_text("Must be a valid code") is True

    def test_starter_with_long_sentence(self):
        assert _is_descriptive_text("Valid loc codes are defined here") is True

    def test_plain_value_list_not_descriptive(self):
        assert _is_descriptive_text("A,B,C") is False


class TestColumnHandling:
    def test_missing_required_columns_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        pd.DataFrame([{"Rule ID": "R1"}]).to_csv(path, index=False)
        with pytest.raises(ValueError, match="Missing required columns"):
            BARulesTemplateConverter().from_csv(str(path))

    def test_alias_columns_normalised(self, tmp_path):
        rows = [
            {
                "rule_id": "R1",
                "rule_name": "N",
                "field": "f",
                "rule_type": "required",
                "severity": "error",
                "expected_values": "",
                "enabled": "Y",
            }
        ]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["id"] == "R1"

    def test_blank_rule_id_row_skipped(self, tmp_path):
        rows = [_row(), _row(**{"Rule ID": None})]
        cfg = _convert(tmp_path, rows)
        assert len(cfg["rules"]) == 1

    def test_unsupported_rule_type_raises(self, tmp_path):
        path = tmp_path / "x.csv"
        pd.DataFrame([_row(**{"Rule Type": "nonsense"})]).to_csv(path, index=False)
        with pytest.raises(ValueError, match="Unsupported Rule Type"):
            BARulesTemplateConverter().from_csv(str(path))


class TestRuleConversions:
    def test_required(self, tmp_path):
        cfg = _convert(tmp_path, [_row()])
        rule = cfg["rules"][0]
        assert rule["type"] == "field_validation"
        assert rule["operator"] == "not_null"
        assert rule["field"] == "acct"

    def test_allowed_values_list(self, tmp_path):
        rows = [_row(**{"Rule Type": "allowed values", "Expected / Values": "A|B|C"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["values"] == ["A", "B", "C"]

    def test_allowed_values_descriptive_text_empties(self, tmp_path):
        rows = [_row(**{"Rule Type": "allowed values", "Expected / Values": "Must be a code"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["values"] == []

    def test_range(self, tmp_path):
        rows = [_row(**{"Rule Type": "range", "Expected / Values": "1..100"})]
        cfg = _convert(tmp_path, rows)
        rule = cfg["rules"][0]
        assert rule["min"] == 1.0 and rule["max"] == 100.0

    def test_range_bad_format_raises(self, tmp_path):
        path = tmp_path / "x.csv"
        pd.DataFrame(
            [_row(**{"Rule Type": "range", "Expected / Values": "nope"})]
        ).to_csv(path, index=False)
        with pytest.raises(ValueError, match="min..max"):
            BARulesTemplateConverter().from_csv(str(path))

    def test_length(self, tmp_path):
        rows = [_row(**{"Rule Type": "length", "Expected / Values": "2..5"})]
        cfg = _convert(tmp_path, rows)
        rule = cfg["rules"][0]
        assert rule["min_length"] == 2 and rule["max_length"] == 5

    def test_regex(self, tmp_path):
        rows = [_row(**{"Rule Type": "regex", "Expected / Values": "^[0-9]+$"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["pattern"] == "^[0-9]+$"

    def test_date_format_maps_to_regex(self, tmp_path):
        rows = [_row(**{"Rule Type": "date format", "Expected / Values": "CCYYMMDD"})]
        cfg = _convert(tmp_path, rows)
        rule = cfg["rules"][0]
        assert rule["pattern"] == r"^\d{8}$"
        assert rule["expected_format"] == "CCYYMMDD"

    def test_compare_fields_cross_field(self, tmp_path):
        rows = [
            _row(
                **{
                    "Rule Type": "compare fields",
                    "Field": "start",
                    "Expected / Values": ">= end_date",
                }
            )
        ]
        cfg = _convert(tmp_path, rows)
        rule = cfg["rules"][0]
        assert rule["type"] == "cross_field"
        assert rule["operator"] == ">="
        assert rule["left_field"] == "start"
        assert rule["right_field"] == "end_date"

    def test_compare_fields_bad_format_raises(self, tmp_path):
        path = tmp_path / "x.csv"
        pd.DataFrame(
            [_row(**{"Rule Type": "compare fields", "Expected / Values": "justone"})]
        ).to_csv(path, index=False)
        with pytest.raises(ValueError, match="<op> <FIELD>"):
            BARulesTemplateConverter().from_csv(str(path))


class TestNativeTypes:
    def test_valid_values_passthrough(self, tmp_path):
        rows = [_row(**{"Rule Type": "valid_values", "Expected / Values": "X,Y"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["values"] == ["X", "Y"]

    def test_min_value_numeric_coercion(self, tmp_path):
        rows = [_row(**{"Rule Type": "min_value", "Expected / Values": "10"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["value"] == 10

    def test_exact_length_float(self, tmp_path):
        rows = [_row(**{"Rule Type": "exact_length", "Expected / Values": "3.0"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["value"] == 3.0

    def test_date_format_native_stores_format(self, tmp_path):
        rows = [_row(**{"Rule Type": "date_format", "Expected / Values": "%Y-%m-%d"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["format"] == "%Y-%m-%d"

    def test_numeric_value_non_numeric_passthrough(self, tmp_path):
        rows = [_row(**{"Rule Type": "numeric", "Expected / Values": "positive"})]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["value"] == "positive"


class TestCrossRow:
    def test_unique_simple_field(self, tmp_path):
        rows = [_row(**{"Rule Type": "cross_row:unique", "Field": "acct"})]
        cfg = _convert(tmp_path, rows)
        rule = cfg["rules"][0]
        assert rule["check"] == "unique"
        assert rule["field"] == "acct"

    def test_key_target_syntax(self, tmp_path):
        rows = [
            _row(**{"Rule Type": "cross_row:consistent", "Field": "key>target"})
        ]
        cfg = _convert(tmp_path, rows)
        rule = cfg["rules"][0]
        assert rule["key_field"] == "key" and rule["target_field"] == "target"

    def test_composite_fields_syntax(self, tmp_path):
        rows = [
            _row(**{"Rule Type": "cross_row:unique_composite", "Field": "a|b|c"})
        ]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["fields"] == ["a", "b", "c"]

    def test_group_count_with_value(self, tmp_path):
        rows = [
            _row(
                **{
                    "Rule Type": "cross_row:group_count",
                    "Field": "grp",
                    "Expected / Values": "5",
                }
            )
        ]
        cfg = _convert(tmp_path, rows)
        assert cfg["rules"][0]["value"] == 5


class TestSave:
    def test_save_requires_conversion(self, tmp_path):
        with pytest.raises(ValueError, match="No rules configuration"):
            BARulesTemplateConverter().save(str(tmp_path / "out.json"))

    def test_save_writes_json(self, tmp_path):
        path = tmp_path / "rules.csv"
        pd.DataFrame([_row()]).to_csv(path, index=False)
        conv = BARulesTemplateConverter(frozen_timestamp="T")
        conv.from_csv(str(path))
        out = tmp_path / "nested" / "rules.json"
        conv.save(str(out))
        data = json.loads(out.read_text())
        assert data["metadata"]["template_type"] == "ba_friendly"
        assert data["metadata"]["created_date"] == "T"
