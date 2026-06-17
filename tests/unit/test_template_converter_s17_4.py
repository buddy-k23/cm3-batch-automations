"""Unit tests for TemplateConverter (S17-4, #432).

Drives the CSV conversion path, format auto-detection, per-row field building,
data-type normalisation, descriptive-text filtering, save, and print_summary.
"""

import json

import pandas as pd
import pytest

from src.config.template_converter import TemplateConverter


def _write_csv(tmp_path, rows, name="tmpl.csv"):
    path = tmp_path / name
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _fixed_width_rows():
    return [
        {
            "Field Name": "id",
            "Data Type": "string",
            "Position": "1",
            "Length": "5",
            "Required": "Y",
            "Target Name": "ID",
            "Description": "the id",
        },
        {
            "Field Name": "amount",
            "Data Type": "number",
            "Position": "6",
            "Length": "10",
            "Required": "N",
            "Valid Values": "10|20|30",
        },
    ]


class TestFromCsv:
    def test_fixed_width_conversion(self, tmp_path):
        path = _write_csv(tmp_path, _fixed_width_rows())
        conv = TemplateConverter(frozen_timestamp="GENERATED")
        mapping = conv.from_csv(path)
        assert mapping["source"]["format"] == "fixed_width"
        assert mapping["total_record_length"] == 15
        assert mapping["metadata"]["created_date"] == "GENERATED"
        assert mapping["metadata"]["last_modified"] == "GENERATED"
        # required field becomes the first key column
        assert mapping["key_columns"] == ["id"]

    def test_fields_have_positions_and_target(self, tmp_path):
        path = _write_csv(tmp_path, _fixed_width_rows())
        mapping = TemplateConverter().from_csv(path)
        id_field = mapping["fields"][0]
        assert id_field["position"] == 1 and id_field["length"] == 5
        assert id_field["target_name"] == "ID"
        assert id_field["required"] is True
        assert {"type": "not_null"} in id_field["validation_rules"]

    def test_valid_values_become_in_list_rule(self, tmp_path):
        path = _write_csv(tmp_path, _fixed_width_rows())
        mapping = TemplateConverter().from_csv(path)
        amt = mapping["fields"][1]
        assert amt["valid_values"] == ["10", "20", "30"]
        assert any(r["type"] == "in_list" for r in amt["validation_rules"])

    def test_missing_required_columns_raises(self, tmp_path):
        path = _write_csv(tmp_path, [{"Field Name": "x"}])  # no Data Type
        with pytest.raises(ValueError, match="Missing required columns"):
            TemplateConverter().from_csv(path)

    def test_snake_case_columns_normalised(self, tmp_path):
        rows = [{"field_name": "x", "data_type": "string"}]
        path = _write_csv(tmp_path, rows)
        mapping = TemplateConverter().from_csv(path)
        assert mapping["fields"][0]["name"] == "x"

    def test_delimited_format_gets_delimiter(self, tmp_path):
        rows = [{"Field Name": "x", "Data Type": "string"}]
        path = _write_csv(tmp_path, rows)
        mapping = TemplateConverter().from_csv(path, file_format="csv")
        assert mapping["source"]["delimiter"] == ","

    def test_fixed_width_missing_length_warning(self, tmp_path):
        rows = [{"Field Name": "x", "Data Type": "string", "Position": "1"}]
        path = _write_csv(tmp_path, rows)
        mapping = TemplateConverter().from_csv(path, file_format="fixed_width")
        assert any("no length" in w for w in mapping["warnings"])


class TestDetectFormat:
    def test_fixed_width_when_position_and_length(self):
        df = pd.DataFrame(columns=["Field Name", "Data Type", "Position", "Length"])
        assert TemplateConverter()._detect_format(df) == "fixed_width"

    def test_pipe_delimited_default(self):
        df = pd.DataFrame(columns=["Field Name", "Data Type"])
        assert TemplateConverter()._detect_format(df) == "pipe_delimited"


class TestNormalizeDataType:
    def test_mappings(self):
        c = TemplateConverter()
        assert c._normalize_data_type("VARCHAR") == "string"
        assert c._normalize_data_type("decimal") == "decimal"
        assert c._normalize_data_type("int") == "integer"
        assert c._normalize_data_type("timestamp") == "date"
        assert c._normalize_data_type("bool") == "boolean"
        assert c._normalize_data_type("weird") == "string"


class TestIsDescriptiveText:
    def test_descriptive_phrases_detected(self):
        assert TemplateConverter._is_descriptive_text("Must be greater than 0") is True
        assert TemplateConverter._is_descriptive_text("see the control table") is True

    def test_long_text_treated_descriptive(self):
        assert TemplateConverter._is_descriptive_text("X" * 61) is True

    def test_actual_values_not_descriptive(self):
        assert TemplateConverter._is_descriptive_text("A,B,C") is False


class TestSaveAndSummary:
    def test_save_requires_conversion_first(self, tmp_path):
        with pytest.raises(ValueError, match="No mapping to save"):
            TemplateConverter().save(str(tmp_path / "out.json"))

    def test_save_writes_json(self, tmp_path):
        path = _write_csv(tmp_path, _fixed_width_rows())
        conv = TemplateConverter(frozen_timestamp="T")
        conv.from_csv(path)
        out = tmp_path / "nested" / "m.json"
        conv.save(str(out))
        assert json.loads(out.read_text())["mapping_name"] == "tmpl"

    def test_print_summary_no_mapping(self, capsys):
        TemplateConverter().print_summary()
        assert "No mapping loaded" in capsys.readouterr().out

    def test_print_summary_fixed_width(self, tmp_path, capsys):
        path = _write_csv(tmp_path, _fixed_width_rows())
        conv = TemplateConverter(frozen_timestamp="T")
        conv.from_csv(path)
        conv.print_summary()
        out = capsys.readouterr().out
        assert "Mapping Summary" in out
        assert "Total record length" in out
