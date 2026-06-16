"""Unit tests for format detector."""

import pytest
import tempfile
import os
from src.parsers.format_detector import FormatDetector, FileFormat
from src.parsers.pipe_delimited_parser import PipeDelimitedParser


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


class TestExtensionRouting:
    """S8-2 (#393): extension-driven routing in ``get_parser_class``.

    Before S8-2, ``FormatDetector.get_parser_class('foo.csv')`` returned
    :class:`PipeDelimitedParser` which read with a hardcoded ``sep="|"``,
    silently mis-parsing every CSV into a single column. The fix makes
    the detector consult the file extension first, and gives the parser a
    ``delimiter`` field that defaults from the extension. These tests
    pin the contract for the three documented extensions.
    """

    def _write(self, suffix: str, body: str) -> str:
        """Write *body* to a temp file with *suffix* and return its path."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix=suffix) as f:
            f.write(body)
            return f.name

    def test_csv_extension_uses_comma_delimiter(self):
        """``.csv`` -> parser whose effective delimiter is comma.

        The parser class is still :class:`PipeDelimitedParser` (the
        repo's general delimited parser), but the parsed DataFrame must
        split on commas — not pipes — so the three header columns land
        in three columns, not one.
        """
        path = self._write('.csv', "id,name,balance\n1,Alice,100\n2,Bob,200\n")
        try:
            detector = FormatDetector()
            cls = detector.get_parser_class(path)
            assert cls is PipeDelimitedParser, cls

            parser = cls(path)
            assert parser.delimiter == ",", parser.delimiter

            df = parser.parse()
            # __source_row__ + 3 data columns from 'id,name,balance'.
            assert len(df.columns) == 4, list(df.columns)
            # First data row (after the header line) should split cleanly.
            second_row = df.iloc[1].tolist()
            assert "Alice" in second_row, second_row
            assert "100" in second_row, second_row
        finally:
            os.unlink(path)

    def test_tsv_extension_uses_tab_delimiter(self):
        """``.tsv`` -> parser whose effective delimiter is tab.

        Mirrors the CSV test with tab separators; guards against any
        future refactor that collapses both extensions to a single hard-
        coded delimiter.
        """
        path = self._write('.tsv', "id\tname\tbalance\n1\tAlice\t100\n2\tBob\t200\n")
        try:
            detector = FormatDetector()
            cls = detector.get_parser_class(path)
            assert cls is PipeDelimitedParser, cls

            parser = cls(path)
            assert parser.delimiter == "\t", repr(parser.delimiter)

            df = parser.parse()
            assert len(df.columns) == 4, list(df.columns)
            second_row = df.iloc[1].tolist()
            assert "Alice" in second_row, second_row
        finally:
            os.unlink(path)

    def test_psv_extension_uses_pipe_delimiter(self):
        """``.psv`` -> parser whose effective delimiter is pipe.

        ``.psv`` ("pipe-separated values") is not heavily used in the
        repo today, but the S8-2 routing table adds it as the explicit
        extension for pipe-delimited content so callers don't have to
        rely on content sniffing. The fallback path for unknown
        extensions still defaults to pipe.
        """
        path = self._write('.psv', "id|name|balance\n1|Alice|100\n2|Bob|200\n")
        try:
            detector = FormatDetector()
            cls = detector.get_parser_class(path)
            assert cls is PipeDelimitedParser, cls

            parser = cls(path)
            assert parser.delimiter == "|", parser.delimiter

            df = parser.parse()
            assert len(df.columns) == 4, list(df.columns)
            second_row = df.iloc[1].tolist()
            assert "Alice" in second_row, second_row
        finally:
            os.unlink(path)

    def test_explicit_delimiter_overrides_extension(self):
        """An explicit ``delimiter`` kwarg wins over extension inference.

        Guards the back-compat escape hatch: a caller that explicitly
        wants to parse a ``.csv`` file with a pipe delimiter (because
        the file is actually mis-named) can still do so.
        """
        path = self._write('.csv', "a|b|c\n1|2|3\n")
        try:
            parser = PipeDelimitedParser(path, delimiter="|")
            assert parser.delimiter == "|"
            df = parser.parse()
            # Four columns: __source_row__ + a,b,c
            assert len(df.columns) == 4, list(df.columns)
        finally:
            os.unlink(path)

    def test_unknown_extension_defaults_to_pipe(self):
        """Files with no/unknown extension fall back to historic pipe default.

        This preserves backwards compatibility with the legacy call
        sites that pass extension-less paths through
        ``PipeDelimitedParser(file_path)`` and expect pipe parsing.
        """
        path = self._write('', "a|b|c\n1|2|3\n")
        try:
            parser = PipeDelimitedParser(path)
            assert parser.delimiter == "|"
        finally:
            os.unlink(path)
