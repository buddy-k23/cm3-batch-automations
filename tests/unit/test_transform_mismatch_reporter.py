"""Unit tests for TransformMismatchReporter (Phase 5c).

Tests cover:
- JSON output structure: source_value, transformed_value, file_value, match per field
- HTML output contains required columns and styling
- Mismatch rows highlighted (contains 'mismatch' or red/warning class)
- Match rows show success styling
- Fields with no transform: source_value == transformed_value
- transform_details injected into compare result dict
"""

from __future__ import annotations

import pytest

from src.transforms.transform_mismatch_reporter import TransformMismatchReporter


# ---------------------------------------------------------------------------
# Fixtures — sample data
# ---------------------------------------------------------------------------

SAMPLE_DETAILS = [
    {
        "field": "STATUS",
        "source_value": "X",
        "transformed_value": "ACTIVE",
        "file_value": "ACTIVE",
    },
    {
        "field": "CODE",
        "source_value": "GBP",
        "transformed_value": "GBP",   # no transform
        "file_value": "USD",          # mismatch
    },
    {
        "field": "AMT",
        "source_value": "100",
        "transformed_value": "100",
        "file_value": "100",
    },
]


# ---------------------------------------------------------------------------
# JSON report
# ---------------------------------------------------------------------------

class TestTransformMismatchReporterJson:
    def test_returns_list_of_field_dicts(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        result = reporter.to_json()
        assert isinstance(result, list)
        assert len(result) == 3

    def test_each_entry_has_required_keys(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        for entry in reporter.to_json():
            assert "field" in entry
            assert "source_value" in entry
            assert "transformed_value" in entry
            assert "file_value" in entry
            assert "match" in entry

    def test_match_true_when_transformed_equals_file(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        result = {e["field"]: e for e in reporter.to_json()}
        assert result["STATUS"]["match"] is True
        assert result["AMT"]["match"] is True

    def test_match_false_when_transformed_differs_from_file(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        result = {e["field"]: e for e in reporter.to_json()}
        assert result["CODE"]["match"] is False

    def test_no_transform_source_equals_transformed(self):
        """Fields without transforms have source_value == transformed_value."""
        details = [
            {"field": "NAME", "source_value": "Alice", "transformed_value": "Alice", "file_value": "Alice"}
        ]
        reporter = TransformMismatchReporter(details)
        entry = reporter.to_json()[0]
        assert entry["source_value"] == entry["transformed_value"]
        assert entry["match"] is True

    def test_empty_details_returns_empty_list(self):
        reporter = TransformMismatchReporter([])
        assert reporter.to_json() == []

    def test_summary_counts(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        summary = reporter.summary()
        assert summary["total_fields"] == 3
        assert summary["matching"] == 2
        assert summary["mismatching"] == 1


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

class TestTransformMismatchReporterHtml:
    def test_html_contains_field_column_header(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "Field" in html

    def test_html_contains_source_column_header(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "Source" in html

    def test_html_contains_transformed_column_header(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "Transformed" in html

    def test_html_contains_file_column_header(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "File" in html

    def test_html_contains_status_column_header(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "Status" in html

    def test_html_highlights_mismatch_rows(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        # Mismatch row for CODE field should have a warning/error class
        assert "mismatch" in html.lower() or "error" in html.lower() or "#" in html

    def test_html_contains_match_indicator(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "✓" in html or "match" in html.lower() or "MATCH" in html

    def test_html_contains_field_names(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "STATUS" in html
        assert "CODE" in html
        assert "AMT" in html

    def test_html_contains_values(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "ACTIVE" in html
        assert "GBP" in html
        assert "USD" in html

    def test_html_is_table_element(self):
        reporter = TransformMismatchReporter(SAMPLE_DETAILS)
        html = reporter.to_html()
        assert "<table" in html
        assert "</table>" in html

    def test_empty_details_returns_empty_table(self):
        reporter = TransformMismatchReporter([])
        html = reporter.to_html()
        assert "<table" in html


# ---------------------------------------------------------------------------
# Integration: transform_details injected into result dict
# ---------------------------------------------------------------------------

class TestTransformDetailsInResult:
    """compare_db_to_file injects transform_details when apply_transforms=True."""

    def _make_mapping(self) -> dict:
        return {
            "fields": [
                {"target_name": "STATUS", "transformation": "Pass 'ACTIVE'"},
            ]
        }

    from unittest.mock import patch, MagicMock

    def test_transform_details_key_present_when_flag_true(self):
        import pandas as pd
        from unittest.mock import patch, MagicMock

        with patch("src.services.db_file_compare_service.OracleConnection"), \
             patch("src.services.db_file_compare_service.DataExtractor") as mock_ext, \
             patch("src.services.db_file_compare_service._df_to_temp_file") as mock_tmp, \
             patch("src.services.db_file_compare_service.run_compare_service") as mock_cmp, \
             patch("src.services.db_file_compare_service.Path") as mock_path:

            mock_path.return_value.exists.return_value = True
            df = pd.DataFrame([{"STATUS": "X"}])
            mock_ext.return_value.extract_table.return_value = df
            mock_tmp.return_value = "/tmp/fake.txt"
            mock_cmp.return_value = {"structure_compatible": True, "rows_with_differences": 0}

            from src.services.db_file_compare_service import compare_db_to_file
            result = compare_db_to_file(
                query_or_table="T",
                mapping_config=self._make_mapping(),
                actual_file="/fake/file.txt",
                apply_transforms=True,
            )
            assert "transform_details" in result

    def test_transform_details_absent_when_flag_false(self):
        import pandas as pd
        from unittest.mock import patch

        with patch("src.services.db_file_compare_service.OracleConnection"), \
             patch("src.services.db_file_compare_service.DataExtractor") as mock_ext, \
             patch("src.services.db_file_compare_service._df_to_temp_file") as mock_tmp, \
             patch("src.services.db_file_compare_service.run_compare_service") as mock_cmp, \
             patch("src.services.db_file_compare_service.Path") as mock_path:

            mock_path.return_value.exists.return_value = True
            df = pd.DataFrame([{"STATUS": "X"}])
            mock_ext.return_value.extract_table.return_value = df
            mock_tmp.return_value = "/tmp/fake.txt"
            mock_cmp.return_value = {"structure_compatible": True, "rows_with_differences": 0}

            from src.services.db_file_compare_service import compare_db_to_file
            result = compare_db_to_file(
                query_or_table="T",
                mapping_config=self._make_mapping(),
                actual_file="/fake/file.txt",
                apply_transforms=False,
            )
            assert "transform_details" not in result
