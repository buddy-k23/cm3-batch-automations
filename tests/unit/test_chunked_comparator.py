"""Unit tests for chunked file comparator."""

import pytest
import tempfile
import os
from src.comparators.chunked_comparator import ChunkedFileComparator


@pytest.fixture
def sample_file1():
    """Create first sample file."""
    content = """id|name|value|status
1|Alice|100|active
2|Bob|200|inactive
3|Charlie|300|active
4|David|400|active
5|Eve|500|inactive"""
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write(content)
        temp_path = f.name
    
    yield temp_path
    
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def sample_file2():
    """Create second sample file with differences."""
    content = """id|name|value|status
1|Alice|150|active
2|Bob|200|active
3|Charlie|300|active
6|Frank|600|active"""
    
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        f.write(content)
        temp_path = f.name
    
    yield temp_path
    
    if os.path.exists(temp_path):
        os.unlink(temp_path)


class TestChunkedFileComparator:
    """Test ChunkedFileComparator class."""
    
    def test_compare_basic(self, sample_file1, sample_file2):
        """Test basic file comparison."""
        comparator = ChunkedFileComparator(
            sample_file1,
            sample_file2,
            key_columns=['id'],
            chunk_size=10
        )
        
        results = comparator.compare(detailed=False, show_progress=False)
        
        assert 'total_rows_file1' in results
        assert 'total_rows_file2' in results
        assert 'differences' in results
        assert results['total_rows_file1'] == 5  # Data rows only (pandas doesn't count header)
        assert results['total_rows_file2'] == 4  # Data rows only
    
    def test_compare_detailed(self, sample_file1, sample_file2):
        """Test detailed comparison with field analysis."""
        comparator = ChunkedFileComparator(
            sample_file1,
            sample_file2,
            key_columns=['id'],
            chunk_size=10
        )
        
        results = comparator.compare(detailed=True, show_progress=False)
        
        assert 'field_statistics' in results
        assert len(results['differences']) > 0
        
        # Check that differences have detailed analysis
        if results['differences']:
            diff = results['differences'][0]
            assert 'keys' in diff
            assert 'differences' in diff
    
    def test_compare_finds_value_differences(self, sample_file1, sample_file2):
        """Test that value differences are detected."""
        comparator = ChunkedFileComparator(
            sample_file1,
            sample_file2,
            key_columns=['id'],
            chunk_size=10
        )
        
        results = comparator.compare(detailed=True, show_progress=False)
        
        # Should find differences in id=1 (value: 100 vs 150) and id=2 (status: inactive vs active)
        assert results['rows_with_differences'] >= 2
    
    def test_compare_with_ignore_columns(self, sample_file1, sample_file2):
        """Test comparison with ignored columns."""
        comparator = ChunkedFileComparator(
            sample_file1,
            sample_file2,
            key_columns=['id'],
            ignore_columns=['status'],
            chunk_size=10
        )
        
        results = comparator.compare(detailed=False, show_progress=False)
        
        # With status ignored, should have fewer differences
        assert 'differences' in results
    
    def test_compare_cleanup(self, sample_file1, sample_file2):
        """Test that temporary database is cleaned up."""
        comparator = ChunkedFileComparator(
            sample_file1,
            sample_file2,
            key_columns=['id'],
            chunk_size=10
        )
        
        temp_db_path = comparator.temp_db_path
        
        # Run comparison
        results = comparator.compare(detailed=False, show_progress=False)
        
        # Cleanup should happen automatically
        comparator._cleanup()
        
        # Temp file should be deleted
        assert not os.path.exists(temp_db_path)
    
    def test_compare_large_chunks(self, sample_file1, sample_file2):
        """Test comparison with small chunk size."""
        comparator = ChunkedFileComparator(
            sample_file1,
            sample_file2,
            key_columns=['id'],
            chunk_size=2  # Very small chunks
        )
        
        results = comparator.compare(detailed=True, show_progress=False)
        
        # Should still work correctly with small chunks
        assert 'total_rows_file1' in results
        assert 'differences' in results
