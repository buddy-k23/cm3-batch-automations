"""Unit tests for file comparator."""

import pytest
import pandas as pd
from src.comparators.file_comparator import FileComparator


class TestFileComparator:
    """Test FileComparator class."""

    def test_compare_identical_files(self):
        """Test comparison of identical DataFrames."""
        df1 = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie'],
            'value': [100, 200, 300]
        })
        df2 = df1.copy()
        
        comparator = FileComparator(df1, df2, key_columns=['id'])
        results = comparator.compare(detailed=False)
        
        assert results['total_rows_file1'] == 3
        assert results['total_rows_file2'] == 3
        assert results['matching_rows'] == 3
        assert len(results['only_in_file1']) == 0
        assert len(results['only_in_file2']) == 0
        assert len(results['differences']) == 0

    def test_compare_with_missing_rows(self):
        """Test comparison with rows missing in file2."""
        df1 = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie']
        })
        df2 = pd.DataFrame({
            'id': [1, 2],
            'name': ['Alice', 'Bob']
        })
        
        comparator = FileComparator(df1, df2, key_columns=['id'])
        results = comparator.compare(detailed=False)
        
        assert len(results['only_in_file1']) == 1
        assert results['only_in_file1']['id'].iloc[0] == 3

    def test_compare_with_extra_rows(self):
        """Test comparison with extra rows in file2."""
        df1 = pd.DataFrame({
            'id': [1, 2],
            'name': ['Alice', 'Bob']
        })
        df2 = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie']
        })
        
        comparator = FileComparator(df1, df2, key_columns=['id'])
        results = comparator.compare(detailed=False)
        
        assert len(results['only_in_file2']) == 1
        assert results['only_in_file2']['id'].iloc[0] == 3

    def test_compare_with_differences(self):
        """Test comparison with value differences."""
        df1 = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie'],
            'value': [100, 200, 300]
        })
        df2 = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie'],
            'value': [100, 250, 300]  # Changed value for id=2
        })
        
        comparator = FileComparator(df1, df2, key_columns=['id'])
        results = comparator.compare(detailed=False)
        
        assert len(results['differences']) == 1
        assert results['differences'][0]['keys']['id'] == 2
        assert 'value' in results['differences'][0]['differences']

    def test_compare_with_ignore_columns(self):
        """Test comparison with ignored columns."""
        df1 = pd.DataFrame({
            'id': [1, 2],
            'name': ['Alice', 'Bob'],
            'timestamp': ['2026-01-01', '2026-01-02']
        })
        df2 = pd.DataFrame({
            'id': [1, 2],
            'name': ['Alice', 'Bob'],
            'timestamp': ['2026-02-01', '2026-02-02']  # Different timestamps
        })
        
        comparator = FileComparator(df1, df2, key_columns=['id'], ignore_columns=['timestamp'])
        results = comparator.compare(detailed=False)
        
        # Should have no differences since timestamp is ignored
        assert len(results['differences']) == 0

    def test_detailed_comparison(self):
        """Test detailed comparison with field analysis."""
        df1 = pd.DataFrame({
            'id': [1, 2],
            'name': ['Alice', 'Bob'],
            'value': [100, 200]
        })
        df2 = pd.DataFrame({
            'id': [1, 2],
            'name': ['ALICE', 'Bob'],  # Case difference
            'value': [150, 200]  # Numeric difference
        })
        
        comparator = FileComparator(df1, df2, key_columns=['id'])
        results = comparator.compare(detailed=True)
        
        assert 'field_statistics' in results
        assert results['rows_with_differences'] == 1
        assert results['field_statistics']['fields_with_differences'] == 2
        
        # Check for detailed analysis
        diff = results['differences'][0]
        assert 'difference_count' in diff

    def test_get_summary(self):
        """Test summary generation."""
        df1 = pd.DataFrame({
            'id': [1, 2, 3],
            'name': ['Alice', 'Bob', 'Charlie']
        })
        df2 = pd.DataFrame({
            'id': [1, 2],
            'name': ['Alice', 'Robert']
        })
        
        comparator = FileComparator(df1, df2, key_columns=['id'])
        summary = comparator.get_summary()
        
        assert "FILE COMPARISON SUMMARY" in summary
        assert "Total Rows" in summary
