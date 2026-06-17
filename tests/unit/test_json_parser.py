"""Unit tests for the NDJSON JsonParser (ADR 0018, S19-1, #395).

Covers: NDJSON line-per-record parsing, JSONPath resolution (scalar /
nested / array-count), ``__source_row__`` 1-indexing, malformed-line
handling, empty-line skipping, and the absent-vs-present-null distinction.
"""

import json
import os
import tempfile

import pandas as pd
import pytest

from src.parsers.json_parser import JsonParser


def _write_ndjson(lines):
    """Write an .ndjson temp file from a list of raw line strings.

    Args:
        lines: Raw line strings (already JSON-serialized or malformed).

    Returns:
        Absolute path to the temp file.
    """
    f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".ndjson")
    f.write("\n".join(lines))
    f.write("\n")
    f.close()
    return f.name


# Field mapping mirrors what TemplateConverter.from_json_template emits:
# a flat list of fields each carrying a ``json_path``.
_FIELDS = [
    {"name": "customer_id", "json_path": "$.customer.id"},
    {"name": "zip", "json_path": "$.customer.address.zip"},
    {"name": "batch_date", "json_path": "$.metadata.batch_date"},
    {"name": "transactions", "json_path": "$.transactions[*]"},
]


class TestJsonParserHappyPath:
    """NDJSON parse + JSONPath resolution."""

    def test_parse_scalar_and_nested(self):
        records = [
            {
                "customer": {"id": "C1", "address": {"zip": "10001"}},
                "metadata": {"batch_date": "2026-06-15"},
                "transactions": [{"amount": 10}, {"amount": 20}],
            },
            {
                "customer": {"id": "C2", "address": {"zip": "94105"}},
                "metadata": {"batch_date": "2026-06-15"},
                "transactions": [{"amount": 5}],
            },
        ]
        path = _write_ndjson([json.dumps(r) for r in records])
        try:
            df = JsonParser(path, _FIELDS).parse()
            assert len(df) == 2
            # Scalar + nested resolution
            assert list(df["customer_id"]) == ["C1", "C2"]
            assert list(df["zip"]) == ["10001", "94105"]
            assert list(df["batch_date"]) == ["2026-06-15", "2026-06-15"]
        finally:
            os.unlink(path)

    def test_array_path_resolves_to_count_column(self):
        records = [
            {"transactions": [{"a": 1}, {"a": 2}, {"a": 3}]},
            {"transactions": []},
        ]
        path = _write_ndjson([json.dumps(r) for r in records])
        try:
            df = JsonParser(path, _FIELDS).parse()
            # ``$.transactions[*]`` collapses to a count column named
            # ``<field>_count`` per ADR 0018 §3.
            assert "transactions_count" in df.columns
            assert list(df["transactions_count"]) == [3, 0]
        finally:
            os.unlink(path)

    def test_source_row_is_one_indexed_line_number(self):
        records = [{"customer": {"id": f"C{i}"}} for i in range(1, 4)]
        path = _write_ndjson([json.dumps(r) for r in records])
        try:
            df = JsonParser(path, [{"name": "customer_id", "json_path": "$.customer.id"}]).parse()
            assert df.columns[0] == "__source_row__"
            assert list(df["__source_row__"]) == [1, 2, 3]
        finally:
            os.unlink(path)


class TestJsonParserEdgeCases:
    """Empty-line skipping, malformed lines, absent-vs-null."""

    def test_empty_and_whitespace_lines_skipped(self):
        records = [{"customer": {"id": "C1"}}, {"customer": {"id": "C2"}}]
        # Interleave blank and whitespace-only lines.
        lines = ["", json.dumps(records[0]), "   ", json.dumps(records[1]), ""]
        path = _write_ndjson(lines)
        try:
            df = JsonParser(path, [{"name": "customer_id", "json_path": "$.customer.id"}]).parse()
            assert len(df) == 2
            assert list(df["customer_id"]) == ["C1", "C2"]
            # __source_row__ reflects the physical line number of each record:
            # record 1 on line 2, record 2 on line 4.
            assert list(df["__source_row__"]) == [2, 4]
        finally:
            os.unlink(path)

    def test_malformed_line_raises_value_error_with_line_number(self):
        path = _write_ndjson([json.dumps({"customer": {"id": "C1"}}), "{not valid json"])
        try:
            with pytest.raises(ValueError) as exc:
                JsonParser(path, [{"name": "customer_id", "json_path": "$.customer.id"}]).parse()
            # The error must be line-addressable (line 2).
            assert "line 2" in str(exc.value).lower()
        finally:
            os.unlink(path)

    def test_absent_vs_present_null_distinction(self):
        records = [
            {"customer": {"id": "C1", "address": {"zip": None}}},  # present-null
            {"customer": {"id": "C2", "address": {}}},  # absent
        ]
        path = _write_ndjson([json.dumps(r) for r in records])
        try:
            df = JsonParser(path, [{"name": "zip", "json_path": "$.customer.address.zip"}]).parse()
            # Present-null -> Python None; absent -> pd.NA. The two are
            # distinguishable so validate_nested_required can flag absence.
            zip_col = df["zip"]
            assert zip_col.iloc[0] is None  # present-with-null
            assert zip_col.iloc[1] is pd.NA  # key missing
        finally:
            os.unlink(path)

    def test_empty_file_yields_empty_dataframe(self):
        path = _write_ndjson([])
        try:
            df = JsonParser(path, _FIELDS).parse()
            assert len(df) == 0
            assert "__source_row__" in df.columns
            assert "customer_id" in df.columns
        finally:
            os.unlink(path)


class TestJsonParserValidateFormat:
    """validate_format() sniffs the first non-blank line for a JSON object."""

    def test_validate_format_true_for_ndjson(self):
        path = _write_ndjson([json.dumps({"a": 1})])
        try:
            assert JsonParser(path, _FIELDS).validate_format() is True
        finally:
            os.unlink(path)

    def test_validate_format_false_for_non_json(self):
        f = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".ndjson")
        f.write("not json at all\n")
        f.close()
        try:
            assert JsonParser(f.name, _FIELDS).validate_format() is False
        finally:
            os.unlink(f.name)
