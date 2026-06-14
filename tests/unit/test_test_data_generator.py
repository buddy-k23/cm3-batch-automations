"""Unit tests for test_data_generator — written before implementation (TDD)."""
from __future__ import annotations

import random
from datetime import datetime

import pytest


@pytest.fixture()
def sample_mapping():
    """Minimal mapping with mixed field types for row/file-level tests."""
    return {
        "mapping_name": "test_sample",
        "source": {"format": "fixed_width"},
        "fields": [
            {"name": "id", "length": 5, "data_type": "string", "required": True,
             "validation_rules": [{"type": "not_null"}]},
            {"name": "status", "length": 1, "valid_values": ["A", "I", "C"]},
            {"name": "amount", "length": 8, "data_type": "decimal"},
            {"name": "eff_date", "length": 8, "data_type": "date",
             "validation_rules": [{"type": "date_format", "parameters": {"format": "%Y%m%d"}}]},
            {"name": "code", "length": 3, "default_value": "001"},
        ],
    }


class TestGenerateFieldValue:
    def test_default_value_used_when_present(self):
        from src.services.test_data_generator import generate_field_value
        field = {"name": "code", "length": 3, "default_value": "001"}
        assert generate_field_value(field, random.Random()).strip() == "001"

    def test_default_value_padded_to_length(self):
        from src.services.test_data_generator import generate_field_value
        field = {"name": "code", "length": 6, "default_value": "001"}
        val = generate_field_value(field, random.Random())
        assert len(val) == 6
        assert val.strip() == "001"

    def test_valid_values_always_in_list(self):
        from src.services.test_data_generator import generate_field_value
        field = {"name": "status", "length": 1, "valid_values": ["A", "I", "C"]}
        for _ in range(100):
            val = generate_field_value(field, random.Random(42))
            assert val.strip() in ["A", "I", "C"]

    def test_numeric_field_zero_padded(self):
        from src.services.test_data_generator import generate_field_value
        field = {"name": "amount", "length": 8, "data_type": "decimal"}
        val = generate_field_value(field, random.Random(1))
        assert len(val) == 8
        assert val.isdigit()

    def test_numeric_field_integer_type(self):
        from src.services.test_data_generator import generate_field_value
        field = {"name": "count", "length": 5, "data_type": "integer"}
        val = generate_field_value(field, random.Random(1))
        assert len(val) == 5
        assert val.isdigit()

    def test_date_field_matches_format_from_validation_rules(self):
        """Real mapping format: validation_rules with type=date_format."""
        from src.services.test_data_generator import generate_field_value
        field = {
            "name": "eff_date", "length": 8,
            "validation_rules": [{"type": "date_format", "parameters": {"format": "%Y%m%d"}}],
        }
        val = generate_field_value(field, random.Random(1))
        datetime.strptime(val.strip(), "%Y%m%d")  # must not raise

    def test_date_field_matches_format_from_rules_shorthand(self):
        """Simplified fixture format: rules with check=date_format."""
        from src.services.test_data_generator import generate_field_value
        field = {
            "name": "eff_date", "length": 8,
            "rules": [{"check": "date_format", "format": "YYYYMMDD"}],
        }
        val = generate_field_value(field, random.Random(1))
        datetime.strptime(val.strip(), "%Y%m%d")  # must not raise

    def test_date_field_from_data_type(self):
        """When data_type is 'date' but no explicit format rule, default to %Y%m%d."""
        from src.services.test_data_generator import generate_field_value
        field = {"name": "some_date", "length": 8, "data_type": "date"}
        val = generate_field_value(field, random.Random(1))
        datetime.strptime(val.strip(), "%Y%m%d")  # must not raise

    def test_not_empty_field_is_not_blank(self):
        """Field with not_null / not_empty rule must produce non-blank value."""
        from src.services.test_data_generator import generate_field_value
        field = {"name": "name", "length": 10, "validation_rules": [{"type": "not_null"}]}
        assert generate_field_value(field, random.Random(1)).strip() != ""

    def test_not_empty_shorthand(self):
        from src.services.test_data_generator import generate_field_value
        field = {"name": "name", "length": 10, "rules": [{"check": "not_empty"}]}
        assert generate_field_value(field, random.Random(1)).strip() != ""

    def test_fallback_random_alphanumeric(self):
        from src.services.test_data_generator import generate_field_value
        field = {"name": "misc", "length": 5}
        val = generate_field_value(field, random.Random(1))
        assert len(val) == 5

    def test_no_length_field_returns_nonempty_string(self):
        """Delimited mappings may omit 'length'. Value should still be generated."""
        from src.services.test_data_generator import generate_field_value
        field = {"name": "email", "data_type": "string"}
        val = generate_field_value(field, random.Random(1))
        assert isinstance(val, str)
        assert len(val) > 0

    def test_numeric_field_length_1_stays_single_digit(self):
        """Numeric fields with length=1 must always produce a single digit, never truncated multi-digit."""
        from src.services.test_data_generator import generate_field_value
        field = {"name": "flag", "length": 1, "data_type": "integer"}
        for seed in range(50):
            val = generate_field_value(field, random.Random(seed))
            assert len(val) == 1, f"seed={seed}: got {val!r} (len={len(val)})"
            assert val.isdigit(), f"seed={seed}: got {val!r}"


class TestGenerateRow:
    def test_row_has_all_fields(self, sample_mapping):
        from src.services.test_data_generator import generate_row
        row = generate_row(sample_mapping["fields"], random.Random())
        assert set(row.keys()) == {f["name"] for f in sample_mapping["fields"]}


class TestGenerateFile:
    def test_row_count(self, sample_mapping):
        from src.services.test_data_generator import generate_file
        rows = generate_file(sample_mapping, row_count=10)
        assert len(rows) == 10

    def test_deterministic_with_seed(self, sample_mapping):
        from src.services.test_data_generator import generate_file
        rows1 = generate_file(sample_mapping, row_count=5, seed=99)
        rows2 = generate_file(sample_mapping, row_count=5, seed=99)
        assert rows1 == rows2

    def test_different_seeds_differ(self, sample_mapping):
        from src.services.test_data_generator import generate_file
        rows1 = generate_file(sample_mapping, row_count=5, seed=1)
        rows2 = generate_file(sample_mapping, row_count=5, seed=2)
        assert rows1 != rows2


class TestInjectErrors:
    """Tests for inject_errors() in test_data_generator."""

    @pytest.fixture()
    def base_rows_and_fields(self):
        """Generate 100 clean rows from a mapping with diverse field types."""
        from src.services.test_data_generator import generate_file
        mapping = {
            "source": {"format": "fixed_width"},
            "fields": [
                {"name": "id", "length": 5, "data_type": "string", "required": True,
                 "validation_rules": [{"type": "not_null"}]},
                {"name": "status", "length": 1, "valid_values": ["A", "I"]},
                {"name": "eff_date", "length": 8, "data_type": "date",
                 "validation_rules": [{"type": "date_format", "parameters": {"format": "%Y%m%d"}}]},
                {"name": "amount", "length": 8, "data_type": "decimal"},
            ],
        }
        rows = generate_file(mapping, row_count=100, seed=42)
        return rows, mapping["fields"]

    def test_blank_required_count(self, base_rows_and_fields):
        from src.services.test_data_generator import inject_errors
        rows, fields = base_rows_and_fields
        result = inject_errors(rows, {"blank_required": 5}, fields, random.Random(1))
        blanked = sum(1 for r in result if r["id"].strip() == "")
        assert blanked == 5

    def test_invalid_date_count(self, base_rows_and_fields):
        from src.services.test_data_generator import inject_errors
        rows, fields = base_rows_and_fields
        result = inject_errors(rows, {"invalid_date": 10}, fields, random.Random(1))
        bad_dates = sum(1 for r in result if "99999999" in r.get("eff_date", ""))
        assert bad_dates == 10

    def test_duplicate_key_count(self, base_rows_and_fields):
        from src.services.test_data_generator import inject_errors
        rows, fields = base_rows_and_fields
        # Count pre-existing natural duplicates of row 0's id before injection
        first_id_before = rows[0]["id"]
        natural_dupes = sum(1 for r in rows[1:] if r["id"] == first_id_before)
        result = inject_errors(rows, {"duplicate_key": 3}, fields, random.Random(1))
        first_id = result[0]["id"]
        dupes = sum(1 for r in result[1:] if r["id"] == first_id)
        # Injected 3 duplicates on top of any natural ones
        assert dupes == natural_dupes + 3

    def test_invalid_value_count(self, base_rows_and_fields):
        from src.services.test_data_generator import inject_errors
        rows, fields = base_rows_and_fields
        result = inject_errors(rows, {"invalid_value": 2}, fields, random.Random(1))
        bad = sum(1 for r in result if r["status"].strip() not in ["A", "I"])
        assert bad >= 2

    def test_wrong_length_count(self, base_rows_and_fields):
        from src.services.test_data_generator import inject_errors
        rows, fields = base_rows_and_fields
        original_len = sum(int(f["length"]) for f in fields)
        result = inject_errors(rows, {"wrong_length": 1}, fields, random.Random(1))
        wrong = sum(
            1 for r in result
            if sum(len(r[f["name"]]) for f in fields) != original_len
        )
        assert wrong == 1

    def test_unknown_error_type_raises(self, base_rows_and_fields):
        from src.services.test_data_generator import inject_errors
        rows, fields = base_rows_and_fields
        with pytest.raises(ValueError, match="Unknown error injection type"):
            inject_errors(rows, {"bogus_type": 1}, fields, random.Random(1))

    def test_zero_injection_no_change(self, base_rows_and_fields):
        from src.services.test_data_generator import inject_errors
        import copy
        rows, fields = base_rows_and_fields
        original = copy.deepcopy(rows)
        result = inject_errors(rows, {}, fields, random.Random(1))
        assert result == original
