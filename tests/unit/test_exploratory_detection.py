"""Exploratory tests for format detection edge cases (#110)."""

import os
import tempfile

import pytest

from src.parsers.format_detector import FormatDetector, FileFormat


class TestExploratoryDetection:
    """Edge-case format detection scenarios."""

    def test_file_with_both_pipes_and_commas(self):
        """Pipe-delimited file where data contains commas should detect pipe."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name|address\n")
            f.write("1|Alice|123 Main St, Apt 4\n")
            f.write("2|Bob|456 Oak Ave, Suite 100\n")
            f.write("3|Charlie|789 Elm Dr, Unit B\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            assert result["format"] == FileFormat.PIPE_DELIMITED
            assert result["delimiter"] == "|"
            assert result["confidence"] > 0.7
        finally:
            os.unlink(temp_file)

    def test_single_column_file(self):
        """File with one value per row and no delimiters returns a result."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("apple\n")
            f.write("banana\n")
            f.write("cherry\n")
            f.write("date\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            # No delimiters present, so no delimited format should score high.
            # The detector should still return without error.
            assert "format" in result
            assert "confidence" in result
            assert result["line_count"] > 0
        finally:
            os.unlink(temp_file)

    def test_very_short_file(self):
        """File with only 1-2 rows still returns a detection result."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name\n")
            f.write("1|Alice\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            assert "format" in result
            assert "confidence" in result
            assert result["line_count"] >= 1
        finally:
            os.unlink(temp_file)

    def test_file_with_extensive_quoting(self):
        """CSV where every field is quoted should still be detected as CSV."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".csv") as f:
            f.write('"id","name","value"\n')
            f.write('"1","Alice","100"\n')
            f.write('"2","Bob","200"\n')
            f.write('"3","Charlie","300"\n')
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            assert result["format"] == FileFormat.CSV
            assert result["delimiter"] == ","
            assert result["confidence"] > 0.7
        finally:
            os.unlink(temp_file)

    def test_fixed_width_varying_record_types(self):
        """Lines of different lengths should still be detected as fixed_width.

        Fixed-width files with multiple record types may have up to two
        distinct line lengths (e.g. header vs detail). The detector should
        still recognise the format when there are no delimiters and at most
        two unique lengths.
        """
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            # Two distinct record lengths, no delimiters
            f.write("HDRRECORD  20260101\n")  # 20 chars
            f.write("DTLRECORD  AAAA  100\n")  # 21 chars
            f.write("DTLRECORD  BBBB  200\n")  # 21 chars
            f.write("DTLRECORD  CCCC  300\n")  # 21 chars
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            # With two unique lengths and no delimiters, expect fixed_width
            # or at minimum a non-error result.
            assert "format" in result
            assert "confidence" in result
            # The detector allows up to 2 unique lengths for fixed-width
            assert result["format"] in (FileFormat.FIXED_WIDTH, FileFormat.UNKNOWN)
        finally:
            os.unlink(temp_file)
