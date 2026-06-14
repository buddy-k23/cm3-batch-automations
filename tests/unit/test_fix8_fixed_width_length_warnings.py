"""Tests for Fix 8: Fixed-width length warnings from TemplateConverter.

When a fixed_width mapping template has fields with missing or zero-length
values, the converter must include a 'warnings' list in its output dict.
"""

import pytest
from src.config.template_converter import TemplateConverter


def test_fixed_width_missing_length_produces_warning(tmp_path):
    """A fixed_width field with no Length value should trigger a warning."""
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Position,Length\n"
        "ACCOUNT_NO,String,1,10\n"
        "STATUS,String,11,\n",  # STATUS has no length
        encoding="utf-8",
    )
    converter = TemplateConverter()
    mapping = converter.from_csv(str(csv_file), mapping_name="fw_test", file_format="fixed_width")

    assert "warnings" in mapping, "Mapping must include 'warnings' key"
    assert len(mapping["warnings"]) > 0, "At least one warning expected for missing length"
    # Warning should mention the field name
    warning_text = " ".join(mapping["warnings"])
    assert "STATUS" in warning_text, f"Warning should reference the 'STATUS' field; got: {warning_text}"


def test_fixed_width_zero_length_produces_warning(tmp_path):
    """A fixed_width field with Length=0 should trigger a warning."""
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Position,Length\n"
        "ACCOUNT_NO,String,1,10\n"
        "STATUS,String,11,0\n",  # STATUS has zero length
        encoding="utf-8",
    )
    converter = TemplateConverter()
    mapping = converter.from_csv(str(csv_file), mapping_name="fw_test", file_format="fixed_width")

    assert "warnings" in mapping, "Mapping must include 'warnings' key"
    assert len(mapping["warnings"]) > 0, "At least one warning expected for zero length"
    warning_text = " ".join(mapping["warnings"])
    assert "STATUS" in warning_text, f"Warning should reference the 'STATUS' field; got: {warning_text}"


def test_fixed_width_no_warnings_when_all_lengths_present(tmp_path):
    """A fully specified fixed_width template should produce no warnings."""
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Position,Length\n"
        "ACCOUNT_NO,String,1,10\n"
        "STATUS,String,11,4\n",
        encoding="utf-8",
    )
    converter = TemplateConverter()
    mapping = converter.from_csv(str(csv_file), mapping_name="fw_test", file_format="fixed_width")

    warnings = mapping.get("warnings", [])
    assert len(warnings) == 0, f"No warnings expected for complete template; got: {warnings}"


def test_pipe_delimited_no_length_warnings(tmp_path):
    """Pipe-delimited mappings should never produce length warnings."""
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type\n"
        "ACCOUNT_NO,String\n"
        "STATUS,String\n",
        encoding="utf-8",
    )
    converter = TemplateConverter()
    mapping = converter.from_csv(str(csv_file), mapping_name="delim_test", file_format="pipe_delimited")

    warnings = mapping.get("warnings", [])
    assert len(warnings) == 0, f"No length warnings expected for pipe-delimited; got: {warnings}"


def test_fixed_width_multiple_missing_lengths_all_warned(tmp_path):
    """All fixed_width fields with missing lengths should appear in warnings."""
    csv_file = tmp_path / "mapping.csv"
    csv_file.write_text(
        "Field Name,Data Type,Position,Length\n"
        "FIELD_A,String,1,\n"
        "FIELD_B,String,2,\n"
        "FIELD_C,String,3,5\n",
        encoding="utf-8",
    )
    converter = TemplateConverter()
    mapping = converter.from_csv(str(csv_file), mapping_name="fw_test", file_format="fixed_width")

    warnings = mapping.get("warnings", [])
    warning_text = " ".join(warnings)
    assert "FIELD_A" in warning_text, "FIELD_A (missing length) should be in warnings"
    assert "FIELD_B" in warning_text, "FIELD_B (missing length) should be in warnings"
    assert "FIELD_C" not in warning_text, "FIELD_C (has length=5) should NOT be in warnings"
