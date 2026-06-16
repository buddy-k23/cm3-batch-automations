"""Parity tests for has_header in the non-chunked delimited parser (S14-4, #414).

The chunked parser defaults ``has_header=True`` and drops the header row, while
the non-chunked :class:`PipeDelimitedParser` historically always read with
``header=None`` — so a header'd CSV/TSV produced an extra data row and a spurious
row-1 violation on the non-chunked path. These tests assert the two paths now
agree, and that the ``__source_row__`` convention matches the chunked path
(data rows numbered from 1; the header line is NOT counted).
"""

import json
from pathlib import Path

from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.services.validate_service import run_validate_service


# Mapping with an explicit header row.
_MAPPING = {
    "mapping_name": "test_has_header_parity",
    "version": "1.0.0",
    "source": {"type": "file", "format": "csv", "delimiter": ",", "has_header": True},
    "fields": [
        {"name": "name", "data_type": "string", "required": True},
        {"name": "age", "data_type": "string", "required": True},
    ],
    "key_columns": ["name"],
}

# A ``not_empty`` rule the rule engine runs identically on BOTH the chunked and
# non-chunked paths — the right vehicle for asserting row-level parity (the
# divergence S14-4 fixes is header handling, not which checks each path runs).
_RULES = {
    "metadata": {"name": "has_header_parity_rules"},
    "rules": [
        {
            "id": "BR1",
            "name": "name not empty",
            "type": "field_validation",
            "severity": "error",
            "operator": "not_empty",
            "field": "name",
            "enabled": True,
        }
    ],
}


def _write(tmp_path: Path, content: str, suffix: str = ".csv", mapping: dict = None):
    """Write a data file, mapping JSON and rules JSON, returning their paths.

    Returns:
        Tuple of (data_file, mapping_file, rules_file) as strings.
    """
    data_file = tmp_path / f"data{suffix}"
    data_file.write_text(content, encoding="utf-8")
    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(json.dumps(mapping or _MAPPING), encoding="utf-8")
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps(_RULES), encoding="utf-8")
    return str(data_file), str(mapping_file), str(rules_file)


class TestParserHasHeader:
    """Unit-level behaviour of the new has_header parameter."""

    def test_has_header_true_skips_header_row(self, tmp_path):
        """With has_header=True the first line is the header, not data."""
        data = tmp_path / "h.csv"
        data.write_text("name,age\nAlice,30\nBob,25\n", encoding="utf-8")
        parser = PipeDelimitedParser(str(data), has_header=True)
        df = parser.parse()
        assert len(df) == 2  # header excluded
        assert list(df["name"]) == ["Alice", "Bob"]
        # __source_row__ numbers data rows from 1 (header NOT counted) — matches chunked.
        assert list(df["__source_row__"]) == [1, 2]

    def test_has_header_true_with_columns_overrides_names(self, tmp_path):
        """Explicit columns + has_header=True drops the header line but uses given names."""
        data = tmp_path / "h.csv"
        data.write_text("ignored1,ignored2\nAlice,30\nBob,25\n", encoding="utf-8")
        parser = PipeDelimitedParser(str(data), columns=["name", "age"], has_header=True)
        df = parser.parse()
        assert len(df) == 2
        assert list(df.columns) == ["__source_row__", "name", "age"]
        assert df["name"].iloc[0] == "Alice"

    def test_has_header_false_preserves_legacy_behaviour(self, tmp_path):
        """has_header=False (and the legacy default) treats the first line as data."""
        data = tmp_path / "h.txt"
        data.write_text("value1|value2\nvalue3|value4\n", encoding="utf-8")
        parser = PipeDelimitedParser(str(data), columns=["a", "b"], has_header=False)
        df = parser.parse()
        assert len(df) == 2
        assert df["a"].iloc[0] == "value1"
        assert list(df["__source_row__"]) == [1, 2]

    def test_default_is_headerless_for_backward_compat(self, tmp_path):
        """Constructing without has_header preserves the historic headerless read."""
        data = tmp_path / "h.txt"
        data.write_text("value1|value2\nvalue3|value4\n", encoding="utf-8")
        parser = PipeDelimitedParser(str(data), columns=["a", "b"])
        df = parser.parse()
        assert len(df) == 2
        assert df["a"].iloc[0] == "value1"


class TestChunkedNonChunkedParity:
    """End-to-end parity through run_validate_service for a header'd file."""

    def test_csv_header_row_count_and_violations_match(self, tmp_path):
        """Chunked and non-chunked must agree on row count + violations for a header'd CSV.

        Before the fix the non-chunked path read the header as data: 4 rows and
        a spurious not_empty violation on the header row.
        """
        content = "name,age\nAlice,30\nBob,25\nCarol,40\n"
        data_file, mapping_file, rules_file = _write(tmp_path, content, suffix=".csv")

        non_chunked = run_validate_service(
            file=data_file, mapping=mapping_file, rules=rules_file, use_chunked=False
        )
        chunked = run_validate_service(
            file=data_file, mapping=mapping_file, rules=rules_file,
            use_chunked=True, chunk_size=2,
        )

        assert non_chunked["total_rows"] == 3
        assert chunked["total_rows"] == 3
        assert non_chunked["total_rows"] == chunked["total_rows"]
        assert non_chunked["error_count"] == chunked["error_count"]
        assert non_chunked["valid"] == chunked["valid"]

    def test_tsv_header_row_count_and_violations_match(self, tmp_path):
        """Parity must also hold for TSV files."""
        mapping = dict(_MAPPING)
        mapping["source"] = {"type": "file", "format": "tsv", "delimiter": "\t", "has_header": True}
        content = "name\tage\nAlice\t30\nBob\t25\nCarol\t40\n"
        data_file, mapping_file, rules_file = _write(
            tmp_path, content, suffix=".tsv", mapping=mapping
        )

        non_chunked = run_validate_service(
            file=data_file, mapping=mapping_file, rules=rules_file, use_chunked=False
        )
        chunked = run_validate_service(
            file=data_file, mapping=mapping_file, rules=rules_file,
            use_chunked=True, chunk_size=2,
        )

        assert non_chunked["total_rows"] == chunked["total_rows"] == 3
        assert non_chunked["error_count"] == chunked["error_count"]
        assert non_chunked["valid"] == chunked["valid"]

    def test_source_row_numbers_agree_between_paths(self, tmp_path):
        """A bad data row must carry the same row number on both paths.

        File line 3 (the row with an empty required ``name``) is data row 2
        (header excluded). Both paths must flag row 2 — not row 3
        (header-counted) and not row 1 (the spurious header-as-data violation
        the bug produced).
        """
        content = "name,age\nAlice,30\n,25\nCarol,40\n"
        data_file, mapping_file, rules_file = _write(tmp_path, content, suffix=".csv")

        non_chunked = run_validate_service(
            file=data_file, mapping=mapping_file, rules=rules_file, use_chunked=False
        )
        chunked = run_validate_service(
            file=data_file, mapping=mapping_file, rules=rules_file,
            use_chunked=True, chunk_size=2,
        )

        nc_rows = sorted({e["row"] for e in non_chunked.get("errors", [])
                          if isinstance(e.get("row"), int)})
        ck_rows = sorted({e["row"] for e in chunked.get("errors", [])
                          if isinstance(e.get("row"), int)})

        assert nc_rows == ck_rows
        assert nc_rows == [2]        # the bad data row, header-excluded numbering
        assert 1 not in nc_rows      # no spurious header-as-data violation
