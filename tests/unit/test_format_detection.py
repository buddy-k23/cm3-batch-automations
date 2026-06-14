"""Tests for FormatDetector — issue #98.

Covers pipe-delimited, CSV, TSV, fixed-width detection and parser class mapping.
"""

import os
import tempfile

import pytest

from src.parsers.format_detector import FileFormat, FormatDetector


class TestFormatDetection:
    """Positive format-detection tests for issue #98."""

    def test_detect_pipe_delimited_format(self):
        """Pipe-delimited file should be detected with confidence >= 0.85."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name|value\n")
            f.write("1|Alice|100\n")
            f.write("2|Bob|200\n")
            f.write("3|Charlie|300\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            assert result["format"] == FileFormat.PIPE_DELIMITED
            assert result["confidence"] >= 0.85
            assert result["delimiter"] == "|"
        finally:
            os.unlink(temp_file)

    def test_detect_csv_format(self):
        """CSV file should be detected with confidence >= 0.85."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            f.write("id,name,value\n")
            f.write("1,Alice,100\n")
            f.write("2,Bob,200\n")
            f.write("3,Charlie,300\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            assert result["format"] == FileFormat.CSV
            assert result["confidence"] >= 0.85
            assert result["delimiter"] == ","
        finally:
            os.unlink(temp_file)

    def test_detect_tsv_format(self):
        """TSV file should be detected with confidence >= 0.85."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".tsv") as f:
            f.write("id\tname\tvalue\n")
            f.write("1\tAlice\t100\n")
            f.write("2\tBob\t200\n")
            f.write("3\tCharlie\t300\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            assert result["format"] == FileFormat.TSV
            assert result["confidence"] >= 0.85
            assert result["delimiter"] == "\t"
        finally:
            os.unlink(temp_file)

    def test_detect_fixed_width_format(self):
        """Fixed-width file should have consistent line lengths and no delimiters."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            # All lines are exactly 16 characters (excluding newline)
            f.write("AAAA  BBBB  CCCC\n")
            f.write("1111  2222  3333\n")
            f.write("XXXX  YYYY  ZZZZ\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            assert result["format"] == FileFormat.FIXED_WIDTH
            assert result["confidence"] >= 0.6
            # Verify that all sample lines have the same length
            assert len(set(len(line) for line in result["sample_lines"])) == 1
        finally:
            os.unlink(temp_file)

    def test_detect_returns_correct_parser_class(self):
        """get_parser_class should return the correct parser for each format."""
        from src.parsers.fixed_width_parser import FixedWidthParser
        from src.parsers.pipe_delimited_parser import PipeDelimitedParser

        # Pipe-delimited -> PipeDelimitedParser
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("a|b|c\n1|2|3\n4|5|6\n")
            pipe_file = f.name

        # Fixed-width -> FixedWidthParser
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("AAAA  BBBB  CCCC\n")
            f.write("1111  2222  3333\n")
            f.write("XXXX  YYYY  ZZZZ\n")
            fw_file = f.name

        # CSV -> PipeDelimitedParser (used with different delimiter)
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            f.write("a,b,c\n1,2,3\n4,5,6\n")
            csv_file = f.name

        try:
            detector = FormatDetector()

            assert detector.get_parser_class(pipe_file) is PipeDelimitedParser
            assert detector.get_parser_class(fw_file) is FixedWidthParser
            assert detector.get_parser_class(csv_file) is PipeDelimitedParser
        finally:
            os.unlink(pipe_file)
            os.unlink(fw_file)
            os.unlink(csv_file)
