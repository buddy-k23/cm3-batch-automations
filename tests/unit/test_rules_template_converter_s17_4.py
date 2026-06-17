"""Unit tests for RulesTemplateConverter (S17-4, #432).

Drives the CSV path (from_csv -> _convert_dataframe -> _convert_row_to_rule)
and the operator-specific parameter builders with real DataFrames.
"""

import json

import pandas as pd
import pytest

from src.config.rules_template_converter import RulesTemplateConverter


def _write_csv(tmp_path, rows):
    df = pd.DataFrame(rows)
    path = tmp_path / "rules.csv"
    df.to_csv(path, index=False)
    return str(path)


def _base_row(**overrides):
    row = {
        "Rule ID": "R1",
        "Rule Name": "Name",
        "Description": "Desc",
        "Type": "field_validation",
        "Severity": "error",
        "Operator": "not_null",
        "Field": "acct",
    }
    row.update(overrides)
    return row


class TestConvertHappyPath:
    def test_not_null_rule(self, tmp_path):
        path = _write_csv(tmp_path, [_base_row()])
        conv = RulesTemplateConverter()
        cfg = conv.from_csv(path)
        assert cfg["metadata"]["name"] == "rules"
        assert len(cfg["rules"]) == 1
        rule = cfg["rules"][0]
        assert rule["id"] == "R1"
        assert rule["field"] == "acct"
        assert rule["enabled"] is True

    def test_numeric_operator_with_value(self, tmp_path):
        path = _write_csv(tmp_path, [_base_row(Operator=">", Value="100")])
        cfg = RulesTemplateConverter().from_csv(path)
        assert cfg["rules"][0]["value"] == 100

    def test_in_operator_splits_values(self, tmp_path):
        path = _write_csv(tmp_path, [_base_row(Operator="in", Values="A, B ,C")])
        cfg = RulesTemplateConverter().from_csv(path)
        assert cfg["rules"][0]["values"] == ["A", "B", "C"]

    def test_regex_operator(self, tmp_path):
        path = _write_csv(tmp_path, [_base_row(Operator="regex", Pattern="^[0-9]+$")])
        cfg = RulesTemplateConverter().from_csv(path)
        assert cfg["rules"][0]["pattern"] == "^[0-9]+$"

    def test_range_operator(self, tmp_path):
        path = _write_csv(tmp_path, [_base_row(Operator="range", Min="1", Max="9")])
        cfg = RulesTemplateConverter().from_csv(path)
        rule = cfg["rules"][0]
        assert rule["min"] == 1 and rule["max"] == 9

    def test_length_operator(self, tmp_path):
        path = _write_csv(
            tmp_path,
            [_base_row(Operator="length", **{"Min Length": "2", "Max Length": "5"})],
        )
        cfg = RulesTemplateConverter().from_csv(path)
        rule = cfg["rules"][0]
        assert rule["min_length"] == 2 and rule["max_length"] == 5

    def test_cross_field_rule(self, tmp_path):
        path = _write_csv(
            tmp_path,
            [
                _base_row(
                    Type="cross_field",
                    Operator="<",
                    **{"Left Field": "start", "Right Field": "end"},
                )
            ],
        )
        cfg = RulesTemplateConverter().from_csv(path)
        rule = cfg["rules"][0]
        assert rule["left_field"] == "start" and rule["right_field"] == "end"

    def test_enabled_flag_parsing(self, tmp_path):
        path = _write_csv(tmp_path, [_base_row(Enabled="NO")])
        cfg = RulesTemplateConverter().from_csv(path)
        assert cfg["rules"][0]["enabled"] is False


class TestSkippingAndValidation:
    def test_missing_required_columns_raises(self, tmp_path):
        df = pd.DataFrame([{"Rule ID": "R1", "Rule Name": "n"}])
        path = tmp_path / "bad.csv"
        df.to_csv(path, index=False)
        with pytest.raises(ValueError, match="Missing required columns"):
            RulesTemplateConverter().from_csv(str(path))

    def test_empty_rule_id_row_skipped(self, tmp_path):
        rows = [_base_row(), _base_row(**{"Rule ID": None})]
        path = _write_csv(tmp_path, rows)
        cfg = RulesTemplateConverter().from_csv(path)
        # second row skipped (empty Rule ID)
        assert len(cfg["rules"]) == 1

    def test_invalid_type_row_skipped(self, tmp_path, capsys):
        rows = [_base_row(), _base_row(**{"Rule ID": "R2", "Type": "bogus"})]
        path = _write_csv(tmp_path, rows)
        cfg = RulesTemplateConverter().from_csv(path)
        # invalid-type row triggers a warning and is skipped
        assert len(cfg["rules"]) == 1
        assert "Skipping" in capsys.readouterr().out

    def test_field_validation_missing_field_skipped(self, tmp_path):
        rows = [_base_row(Field=None)]
        path = _write_csv(tmp_path, rows)
        cfg = RulesTemplateConverter().from_csv(path)
        assert len(cfg["rules"]) == 0


class TestValidateTemplate:
    def test_valid_template(self):
        df = pd.DataFrame([_base_row()])
        result = RulesTemplateConverter().validate_template(df)
        assert result["valid"] is True

    def test_missing_columns_reported(self):
        df = pd.DataFrame([{"Rule ID": "R1"}])
        result = RulesTemplateConverter().validate_template(df)
        assert result["valid"] is False
        assert any("Missing required" in e for e in result["errors"])

    def test_duplicate_rule_ids(self):
        df = pd.DataFrame([_base_row(), _base_row()])
        result = RulesTemplateConverter().validate_template(df)
        assert any("Duplicate Rule IDs" in e for e in result["errors"])

    def test_empty_value_warning(self):
        rows = [_base_row(), _base_row(**{"Rule ID": "R2", "Severity": None})]
        df = pd.DataFrame(rows)
        result = RulesTemplateConverter().validate_template(df)
        assert any("empty values" in w for w in result["warnings"])


class TestParseValueAndSave:
    def test_parse_value_int_float_string(self):
        conv = RulesTemplateConverter()
        assert conv._parse_value("12") == 12
        assert conv._parse_value("1.5") == 1.5
        assert conv._parse_value("abc") == "abc"
        assert conv._parse_value(pd.NA) is None

    def test_save_requires_config_first(self, tmp_path):
        conv = RulesTemplateConverter()
        with pytest.raises(ValueError, match="No rules configuration"):
            conv.save(str(tmp_path / "out.json"))

    def test_save_writes_json(self, tmp_path):
        path = _write_csv(tmp_path, [_base_row()])
        conv = RulesTemplateConverter()
        conv.from_csv(path)
        out = tmp_path / "nested" / "rules.json"
        conv.save(str(out))
        assert out.exists()
        assert "rules" in json.loads(out.read_text())
