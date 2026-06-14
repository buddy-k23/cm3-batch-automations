"""Unit tests for chunked file parser."""

import pytest
import pandas as pd
import tempfile
import os
from pathlib import Path
from src.parsers.chunked_parser import ChunkedFileParser, ChunkedFixedWidthParser


@pytest.fixture
def sample_pipe_file():
    """Create a sample pipe-delimited file."""
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
    
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def large_pipe_file():
    """Create a large pipe-delimited file for testing."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
        # Write header
        f.write("id|name|value|status\n")
        
        # Write 1000 rows
        for i in range(1000):
            f.write(f"{i}|User{i}|{i*100}|active\n")
        
        temp_path = f.name
    
    yield temp_path
    
    # Cleanup
    if os.path.exists(temp_path):
        os.unlink(temp_path)


class TestChunkedFileParser:
    """Test ChunkedFileParser class."""
    
    def test_parse_chunks_small_file(self, sample_pipe_file):
        """Test parsing small file in chunks."""
        parser = ChunkedFileParser(sample_pipe_file, delimiter='|', chunk_size=2)
        
        chunks = list(parser.parse_chunks())
        
        assert len(chunks) == 3  # 5 data rows / 2 per chunk = 3 chunks (2, 2, 1)
        assert len(chunks[0]) == 2
        assert len(chunks[1]) == 2
        assert len(chunks[2]) == 1  # Last chunk has 1 row
    
    def test_parse_chunks_with_columns(self, sample_pipe_file):
        """Test parsing with specified columns."""
        parser = ChunkedFileParser(sample_pipe_file, delimiter='|', chunk_size=10)
        columns = ['id', 'name', 'value', 'status']
        
        chunks = list(parser.parse_chunks(columns=columns))
        
        assert len(chunks) == 1
        assert list(chunks[0].columns) == columns
    
    def test_count_rows(self, sample_pipe_file):
        """Test row counting."""
        parser = ChunkedFileParser(sample_pipe_file, delimiter='|')
        
        row_count = parser.count_rows()
        
        assert row_count == 6  # 5 data rows + 1 header
    
    def test_get_file_info(self, sample_pipe_file):
        """Test file info retrieval."""
        parser = ChunkedFileParser(sample_pipe_file, delimiter='|', chunk_size=100)
        
        info = parser.get_file_info()
        
        assert 'file_path' in info
        assert 'size_bytes' in info
        assert 'total_rows' in info
        assert info['chunk_size'] == 100
        assert info['delimiter'] == '|'
    
    def test_parse_sample(self, large_pipe_file):
        """Test sample parsing."""
        parser = ChunkedFileParser(large_pipe_file, delimiter='|')
        
        sample = parser.parse_sample(n_rows=10)
        
        assert len(sample) == 10
        assert 'id' in sample.columns
    
    def test_validate_structure_valid_file(self, sample_pipe_file):
        """Test structure validation on valid file."""
        parser = ChunkedFileParser(sample_pipe_file, delimiter='|')
        
        result = parser.validate_structure()
        
        assert result['valid'] is True
        assert len(result['errors']) == 0
    
    def test_validate_structure_missing_file(self):
        """Test structure validation on missing file."""
        parser = ChunkedFileParser('/nonexistent/file.txt', delimiter='|')
        
        result = parser.validate_structure()
        
        assert result['valid'] is False
        assert len(result['errors']) > 0
    
    def test_parse_with_progress(self, large_pipe_file):
        """Test parsing with progress tracking."""
        parser = ChunkedFileParser(large_pipe_file, delimiter='|', chunk_size=100)
        
        progress_updates = []
        
        def progress_callback(current, total):
            progress_updates.append((current, total))
        
        chunks = list(parser.parse_with_progress(progress_callback=progress_callback))
        
        assert len(chunks) > 0
        assert len(progress_updates) > 0
        # Progress callback gets total from count_rows (includes header)
        # but actual data rows processed is 1000
        assert progress_updates[-1][0] >= 1000  # At least 1000 rows processed
    
    def test_large_file_memory_efficiency(self, large_pipe_file):
        """Test that chunked parsing doesn't load entire file."""
        parser = ChunkedFileParser(large_pipe_file, delimiter='|', chunk_size=100)
        
        # Process chunks one at a time
        total_rows = 0
        for chunk in parser.parse_chunks():
            total_rows += len(chunk)
            # Each chunk should be small
            assert len(chunk) <= 100
        
        assert total_rows == 1000  # 1000 data rows (header not counted by pandas)


class TestChunkedFixedWidthParser:
    """Test ChunkedFixedWidthParser class."""
    
    @pytest.fixture
    def sample_fixed_width_file(self):
        """Create a sample fixed-width file."""
        content = """001Alice    100active  
002Bob      200inactive
003Charlie  300active  """
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(content)
            temp_path = f.name
        
        yield temp_path
        
        # Cleanup
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    
    def test_parse_fixed_width_chunks(self, sample_fixed_width_file):
        """Test parsing fixed-width file in chunks."""
        field_specs = [
            ('id', 0, 3),
            ('name', 3, 11),
            ('value', 11, 14),
            ('status', 14, 22)
        ]
        
        parser = ChunkedFixedWidthParser(
            sample_fixed_width_file,
            field_specs=field_specs,
            chunk_size=2
        )
        
        chunks = list(parser.parse_chunks())
        
        assert len(chunks) == 2  # 3 rows / 2 per chunk
        assert 'id' in chunks[0].columns
        assert 'name' in chunks[0].columns
