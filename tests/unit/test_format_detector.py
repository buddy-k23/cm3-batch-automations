"""Unit tests for format detector."""

import pytest
import tempfile
import os
from src.parsers.format_detector import FormatDetector, FileFormat


class TestFormatDetector:
    """Test FormatDetector class."""

    def test_detect_pipe_delimited(self):
        """Test detection of pipe-delimited format."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("col1|col2|col3\n")
            f.write("val1|val2|val3\n")
            f.write("val4|val5|val6\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)
            
            assert result['format'] == FileFormat.PIPE_DELIMITED
            assert result['confidence'] > 0.9
            assert result['delimiter'] == '|'
        finally:
            os.unlink(temp_file)

    def test_detect_csv(self):
        """Test detection of CSV format."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
            f.write("col1,col2,col3\n")
            f.write("val1,val2,val3\n")
            f.write("val4,val5,val6\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)
            
            assert result['format'] == FileFormat.CSV
            assert result['confidence'] > 0.8
            assert result['delimiter'] == ','
        finally:
            os.unlink(temp_file)

    def test_detect_fixed_width(self):
        """Test detection of fixed-width format."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("AAAA  BBBB  CCCC\n")
            f.write("1111  2222  3333\n")
            f.write("XXXX  YYYY  ZZZZ\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)
            
            assert result['format'] == FileFormat.FIXED_WIDTH
            assert result['confidence'] > 0.6
        finally:
            os.unlink(temp_file)

    def test_detect_file_not_found(self):
        """Test detection with non-existent file."""
        detector = FormatDetector()
        
        with pytest.raises(FileNotFoundError):
            detector.detect('nonexistent_file.txt')

    def test_detect_empty_file(self):
        """Test detection with empty file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)
            
            assert result['format'] == FileFormat.UNKNOWN
            assert result['confidence'] == 0.0
        finally:
            os.unlink(temp_file)
