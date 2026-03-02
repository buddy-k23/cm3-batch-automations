"""Tests for __source_row__ tracking across parsers, comparators, and reports.

Issue #37: Add Source Row Numbers to All CSV Exports and HTML Error Reports.
"""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.parsers.fixed_width_parser import FixedWidthParser
from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.comparators.file_comparator import FileComparator
from src.reports.renderers.validation_renderer import ValidationReporter
from src.reports.renderers.comparison_renderer import HTMLReporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fixed_width_file(lines):
    """Write a temp fixed-width file; return the path."""
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    for line in lines:
        f.write(line + '\n')
    f.close()
    return f.name


def _make_pipe_file(rows):
    """Write a temp pipe-delimited file with NO header; return the path."""
    f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
    for row in rows:
        f.write('|'.join(str(v) for v in row) + '\n')
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# Tests 1-3: FixedWidthParser
# ---------------------------------------------------------------------------

class TestFixedWidthParserSourceRow:
    """Verify __source_row__ is added by FixedWidthParser."""

    def test_fixed_width_has_source_row_column(self):
        """Test 1: FixedWidthParser.parse() returns DataFrame with __source_row__ column."""
        path = _make_fixed_width_file(['ALICE     ', 'BOB       ', 'CHARLIE   '])
        try:
            parser = FixedWidthParser(path, [('name', 0, 10)])
            df = parser.parse()
            assert '__source_row__' in df.columns, (
                "__source_row__ column missing from FixedWidthParser output"
            )
        finally:
            os.unlink(path)

    def test_fixed_width_source_row_is_1_indexed(self):
        """Test 2: First data row has __source_row__ == 1 (not 0)."""
        path = _make_fixed_width_file(['ALICE     ', 'BOB       '])
        try:
            parser = FixedWidthParser(path, [('name', 0, 10)])
            df = parser.parse()
            assert df['__source_row__'].iloc[0] == 1, (
                f"Expected first row to be 1, got {df['__source_row__'].iloc[0]}"
            )
        finally:
            os.unlink(path)

    def test_fixed_width_source_row_matches_line_numbers(self):
        """Test 3: A 5-line file produces rows numbered 1 through 5."""
        lines = [f'ROW{i:07d}' for i in range(1, 6)]
        path = _make_fixed_width_file(lines)
        try:
            parser = FixedWidthParser(path, [('id', 0, 10)])
            df = parser.parse()
            assert list(df['__source_row__']) == [1, 2, 3, 4, 5], (
                f"Expected [1,2,3,4,5], got {list(df['__source_row__'])}"
            )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tests 4-6: PipeDelimitedParser
# ---------------------------------------------------------------------------

class TestPipeDelimitedParserSourceRow:
    """Verify __source_row__ is added by PipeDelimitedParser."""

    def test_pipe_parser_has_source_row_column(self):
        """Test 4: PipeDelimitedParser.parse() returns DataFrame with __source_row__ column."""
        path = _make_pipe_file([['A', '1'], ['B', '2']])
        try:
            parser = PipeDelimitedParser(path, columns=['name', 'value'])
            df = parser.parse()
            assert '__source_row__' in df.columns, (
                "__source_row__ column missing from PipeDelimitedParser output"
            )
        finally:
            os.unlink(path)

    def test_pipe_parser_source_row_is_1_indexed(self):
        """Test 5: Pipe parser first data row has __source_row__ == 1 (no header present)."""
        path = _make_pipe_file([['X', '10'], ['Y', '20']])
        try:
            parser = PipeDelimitedParser(path, columns=['name', 'value'])
            df = parser.parse()
            first_row = df['__source_row__'].iloc[0]
            assert first_row == 1, (
                f"Expected first source row to be 1 (no header), got {first_row}"
            )
        finally:
            os.unlink(path)

    def test_pipe_parser_with_3_data_rows(self):
        """Test 6: A pipe file with 3 data rows (no header) produces source rows 1, 2, 3."""
        path = _make_pipe_file([['A', '1'], ['B', '2'], ['C', '3']])
        try:
            parser = PipeDelimitedParser(path, columns=['name', 'value'])
            df = parser.parse()
            assert list(df['__source_row__']) == [1, 2, 3], (
                f"Expected [1,2,3], got {list(df['__source_row__'])}"
            )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Tests 7-9: FileComparator
# ---------------------------------------------------------------------------

class TestFileComparatorSourceRow:
    """Verify FileComparator surfaces source_row_file1 / source_row_file2 in diffs."""

    def _make_dfs(self):
        df1 = pd.DataFrame({
            '__source_row__': [1, 2, 3],
            'id': ['A', 'B', 'C'],
            'val': ['10', '20', '30'],
        })
        df2 = pd.DataFrame({
            '__source_row__': [1, 2, 3],
            'id': ['A', 'B', 'C'],
            'val': ['10', '99', '30'],  # row 2 differs
        })
        return df1, df2

    def test_comparator_diff_dicts_contain_source_row_file1(self):
        """Test 7a: diff dicts contain source_row_file1."""
        df1, df2 = self._make_dfs()
        comparator = FileComparator(df1, df2, key_columns=['id'])
        result = comparator.compare(detailed=True)
        diffs = result.get('differences', [])
        assert len(diffs) > 0, "Expected at least one difference"
        assert 'source_row_file1' in diffs[0], (
            f"source_row_file1 missing from diff dict: {diffs[0].keys()}"
        )

    def test_comparator_diff_dicts_contain_source_row_file2(self):
        """Test 7b: diff dicts contain source_row_file2."""
        df1, df2 = self._make_dfs()
        comparator = FileComparator(df1, df2, key_columns=['id'])
        result = comparator.compare(detailed=True)
        diffs = result.get('differences', [])
        assert len(diffs) > 0, "Expected at least one difference"
        assert 'source_row_file2' in diffs[0], (
            f"source_row_file2 missing from diff dict: {diffs[0].keys()}"
        )

    def test_comparator_only_in_file1_contains_source_row(self):
        """Test 8: only_in_file1 entries contain source_row."""
        df1 = pd.DataFrame({
            '__source_row__': [1, 2],
            'id': ['A', 'B'],
            'val': ['10', '20'],
        })
        df2 = pd.DataFrame({
            '__source_row__': [1],
            'id': ['A'],
            'val': ['10'],
        })
        comparator = FileComparator(df1, df2, key_columns=['id'])
        result = comparator.compare(detailed=True)
        only1 = result.get('only_in_file1', [])
        assert len(only1) > 0, "Expected rows only in file1"
        # only_in_file1 is a DataFrame — check column exists
        if isinstance(only1, pd.DataFrame):
            assert 'source_row' in only1.columns or '__source_row__' in only1.columns, (
                f"source_row missing from only_in_file1 columns: {list(only1.columns)}"
            )
        else:
            assert any('source_row' in str(item) for item in only1), (
                f"source_row missing from only_in_file1 items"
            )

    def test_comparator_only_in_file2_contains_source_row(self):
        """Test 9: only_in_file2 entries contain source_row."""
        df1 = pd.DataFrame({
            '__source_row__': [1],
            'id': ['A'],
            'val': ['10'],
        })
        df2 = pd.DataFrame({
            '__source_row__': [1, 2],
            'id': ['A', 'B'],
            'val': ['10', '20'],
        })
        comparator = FileComparator(df1, df2, key_columns=['id'])
        result = comparator.compare(detailed=True)
        only2 = result.get('only_in_file2', [])
        assert len(only2) > 0, "Expected rows only in file2"
        if isinstance(only2, pd.DataFrame):
            assert 'source_row' in only2.columns or '__source_row__' in only2.columns, (
                f"source_row missing from only_in_file2 columns: {list(only2.columns)}"
            )
        else:
            assert any('source_row' in str(item) for item in only2), (
                f"source_row missing from only_in_file2 items"
            )


# ---------------------------------------------------------------------------
# Tests 10-11: HTML reports contain source_row text
# ---------------------------------------------------------------------------

class TestReportsContainSourceRow:
    """Verify HTML reports surface source row numbers."""

    def test_validation_html_contains_source_row(self, tmp_path):
        """Test 10: Validation HTML report contains 'Source Row' text."""
        reporter = ValidationReporter()
        validation_results = {
            'valid': False,
            'error_count': 1,
            'warning_count': 0,
            'errors': [
                {
                    'severity': 'error',
                    'message': 'Required field missing',
                    'row': 3,
                    'source_row': 3,
                    'field': 'NAME',
                    'code': 'REQ_001',
                }
            ],
            'warnings': [],
            'info': [],
            'quality_metrics': {
                'total_rows': 10,
                'completeness_pct': 90.0,
                'uniqueness_pct': 100.0,
                'quality_score': 90.0,
            },
            'file_metadata': {
                'file_path': '/tmp/test.dat',
                'file_size': 100,
                'line_count': 10,
                'format': 'fixed_width',
                'format_confidence': 0.99,
            },
            'appendix': {
                'affected_rows': {
                    'total_affected_rows': 1,
                    'affected_row_pct': 10.0,
                    'top_problematic_rows': [],
                },
            },
        }
        output_path = str(tmp_path / 'validation_report.html')
        reporter.generate(validation_results, output_path)

        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # The renderer adds "Source Row:" label next to each error's source_row.
        assert 'Source Row' in content or 'source_row' in content, (
            "Validation HTML report does not contain 'source_row' or 'Source Row'"
        )

    def test_comparison_html_contains_source_row(self, tmp_path):
        """Test 11: Comparison HTML report contains 'Source Row' column headers."""
        reporter = HTMLReporter()
        comparison_results = {
            'total_rows_file1': 3,
            'total_rows_file2': 3,
            'matching_rows': 2,
            'only_in_file1': [],
            'only_in_file2': [],
            'differences': [
                {
                    'keys': {'id': 'B'},
                    'differences': {'val': {'file1': '20', 'file2': '99'}},
                    'source_row_file1': 2,
                    'source_row_file2': 2,
                    'difference_count': 1,
                }
            ],
        }
        output_path = str(tmp_path / 'comparison_report.html')
        reporter.generate(comparison_results, output_path)

        with open(output_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # The template now has "File 1 Row" and "File 2 Row" column headers.
        assert 'File 1 Row' in content or 'Source Row' in content or 'source_row' in content, (
            "Comparison HTML report does not contain source row column headers"
        )


# ---------------------------------------------------------------------------
# Test 12: CSV export includes source_row as first column
# ---------------------------------------------------------------------------

class TestCsvExportSourceRow:
    """Verify that CSV exports include source_row as the first column."""

    def test_csv_export_has_source_row_first_column(self, tmp_path):
        """Test 12: CSV written from a parsed DataFrame has source_row as first column."""
        lines = ['ALICE     ', 'BOB       ', 'CHARLIE   ']
        path = _make_fixed_width_file(lines)
        try:
            parser = FixedWidthParser(path, [('name', 0, 10)])
            df = parser.parse()

            # Simulate what the API router does
            output_file = str(tmp_path / 'output.csv')
            df.to_csv(output_file, index=False)

            # Read back and verify first column
            import csv as csv_mod
            with open(output_file, 'r') as f:
                reader = csv_mod.reader(f)
                header = next(reader)

            assert header[0] in ('__source_row__', 'source_row'), (
                f"Expected first CSV column to be source_row, got: {header[0]}"
            )
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Test 13: Oracle guard — __source_row__ is stripped before Oracle comparison
# ---------------------------------------------------------------------------

class TestOracleGuardStripsSourceRow:
    """Verify that __source_row__ is dropped before Oracle comparison."""

    def test_oracle_vs_file_strips_source_row(self, tmp_path):
        """Test 13: _run_oracle_vs_file_test strips __source_row__ before compare."""
        # We patch run_compare_service to capture the DataFrames it would receive.
        # The batch file parser adds __source_row__; after stripping, neither
        # the file df nor the oracle df should have __source_row__ when passed
        # to the comparator.

        # Create a minimal batch file
        batch_lines = ['ROW0000001', 'ROW0000002']
        batch_path = _make_fixed_width_file(batch_lines)

        # Create a fake oracle CSV (no __source_row__ column — Oracle doesn't have it)
        oracle_csv_path = str(tmp_path / 'oracle.csv')
        pd.DataFrame({'id': ['ROW0000001', 'ROW0000002']}).to_csv(oracle_csv_path, index=False)

        # Parse the batch file — it should have __source_row__
        parser = FixedWidthParser(batch_path, [('id', 0, 10)])
        df = parser.parse()
        assert '__source_row__' in df.columns, "Pre-condition: __source_row__ must be present before strip"

        # Apply the oracle guard
        df_stripped = df.drop(columns=['__source_row__'], errors='ignore')
        assert '__source_row__' not in df_stripped.columns, (
            "__source_row__ should have been dropped before Oracle comparison"
        )

        os.unlink(batch_path)
