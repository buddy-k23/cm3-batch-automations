"""Automatic file format detection."""

import os
from typing import Optional, Dict, Any
from enum import Enum


class FileFormat(Enum):
    """Supported file formats."""
    PIPE_DELIMITED = "pipe_delimited"
    FIXED_WIDTH = "fixed_width"
    CSV = "csv"
    TSV = "tsv"
    JSON = "json"  # NDJSON / .jsonl — one JSON object per line (ADR 0018).
    UNKNOWN = "unknown"


class FormatDetector:
    """Detects file format automatically."""

    def __init__(self, sample_size: int = 1000):
        """Initialize format detector.
        
        Args:
            sample_size: Number of bytes to read for detection
        """
        self.sample_size = sample_size

    def detect(self, file_path: str) -> Dict[str, Any]:
        """Detect file format.
        
        Args:
            file_path: Path to file
            
        Returns:
            Dictionary with format info:
            {
                'format': FileFormat,
                'delimiter': str (if applicable),
                'confidence': float (0-1),
                'line_count': int,
                'sample_lines': list
            }
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            # Read sample
            sample = f.read(self.sample_size)
            f.seek(0)
            sample_lines = [f.readline().rstrip('\n') for _ in range(10)]
            sample_lines = [line for line in sample_lines if line]  # Remove empty

        if not sample_lines:
            return {
                'format': FileFormat.UNKNOWN,
                'confidence': 0.0,
                'line_count': 0,
                'sample_lines': []
            }

        # Detect format
        format_scores = {
            FileFormat.PIPE_DELIMITED: self._score_pipe_delimited(sample_lines),
            FileFormat.CSV: self._score_csv(sample_lines),
            FileFormat.TSV: self._score_tsv(sample_lines),
            FileFormat.FIXED_WIDTH: self._score_fixed_width(sample_lines),
            FileFormat.JSON: self._score_json(sample_lines),
        }

        # Get best match
        best_format = max(format_scores, key=format_scores.get)
        confidence = format_scores[best_format]

        result = {
            'format': best_format if confidence > 0.5 else FileFormat.UNKNOWN,
            'confidence': confidence,
            'line_count': len(sample_lines),
            'sample_lines': sample_lines[:3],
        }

        # Add delimiter info
        if best_format == FileFormat.PIPE_DELIMITED:
            result['delimiter'] = '|'
        elif best_format == FileFormat.CSV:
            result['delimiter'] = ','
        elif best_format == FileFormat.TSV:
            result['delimiter'] = '\t'

        return result

    def _score_pipe_delimited(self, lines: list) -> float:
        """Score likelihood of pipe-delimited format."""
        if not lines:
            return 0.0

        pipe_counts = [line.count('|') for line in lines]
        
        # Check consistency
        if len(set(pipe_counts)) == 1 and pipe_counts[0] > 0:
            return 0.95
        elif len(set(pipe_counts)) <= 2 and min(pipe_counts) > 0:
            return 0.75
        elif any(count > 0 for count in pipe_counts):
            return 0.5
        return 0.0

    def _score_csv(self, lines: list) -> float:
        """Score likelihood of CSV format."""
        if not lines:
            return 0.0

        comma_counts = [line.count(',') for line in lines]
        
        # Check consistency
        if len(set(comma_counts)) == 1 and comma_counts[0] > 0:
            # Check if not pipe-delimited
            pipe_counts = [line.count('|') for line in lines]
            if max(pipe_counts) == 0:
                return 0.9
            return 0.6
        elif len(set(comma_counts)) <= 2 and min(comma_counts) > 0:
            return 0.7
        elif any(count > 0 for count in comma_counts):
            return 0.4
        return 0.0

    def _score_tsv(self, lines: list) -> float:
        """Score likelihood of TSV format."""
        if not lines:
            return 0.0

        tab_counts = [line.count('\t') for line in lines]
        
        # Check consistency
        if len(set(tab_counts)) == 1 and tab_counts[0] > 0:
            return 0.9
        elif len(set(tab_counts)) <= 2 and min(tab_counts) > 0:
            return 0.7
        elif any(count > 0 for count in tab_counts):
            return 0.4
        return 0.0

    def _score_fixed_width(self, lines: list) -> float:
        """Score likelihood of fixed-width format."""
        if not lines or len(lines) < 3:
            return 0.0

        # Check if lines have consistent length
        line_lengths = [len(line) for line in lines]
        unique_lengths = set(line_lengths)

        # Fixed-width files have very consistent line lengths
        if len(unique_lengths) == 1:
            # Check if no common delimiters
            has_pipes = any('|' in line for line in lines)
            has_commas = any(',' in line for line in lines)
            has_tabs = any('\t' in line for line in lines)
            
            if not (has_pipes or has_commas or has_tabs):
                return 0.85
            return 0.3
        elif len(unique_lengths) <= 2:
            # Allow for last line variation
            return 0.6
        return 0.2

    def _score_json(self, lines: list) -> float:
        """Score likelihood of NDJSON format from the first non-blank line.

        NDJSON has one JSON object per line, so the first non-blank line
        starts with ``{``. A first line starting with ``[`` signals a
        top-level JSON array (plain ``.json``), which v1 does **not**
        support — that case is flagged for the caller in
        :meth:`describe_json_array_hint`, not scored as JSON here.

        Args:
            lines: Sample non-blank lines from the file.

        Returns:
            ``0.9`` when the first non-blank line begins with ``{`` (an
            NDJSON record), otherwise ``0.0``.
        """
        if not lines:
            return 0.0
        first = lines[0].lstrip()
        return 0.9 if first.startswith("{") else 0.0

    @staticmethod
    def _looks_like_json_array(file_path: str) -> bool:
        """Return True if the file's first non-blank char is ``[``.

        A top-level JSON array (plain ``.json``) is the unsupported v1 case
        — the parser is NDJSON-only. This sniff lets the router raise a
        clear "convert to NDJSON first" error instead of a confusing parse
        failure.

        Args:
            file_path: Path whose leading content is inspected.

        Returns:
            ``True`` when the first non-whitespace character is ``[``.
        """
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                while True:
                    chunk = f.read(64)
                    if not chunk:
                        return False
                    stripped = chunk.lstrip()
                    if stripped:
                        return stripped[0] == "["
        except Exception:
            return False

    # Extension-driven routing table. Consulted before content sniffing so
    # ``.csv`` / ``.tsv`` / ``.psv`` files route deterministically to a
    # delimited parser with the correct separator, instead of being
    # mis-routed through ``PipeDelimitedParser`` with ``sep="|"`` whenever
    # the content sniffer's heuristic mis-fires (S8-2, #393). The detector
    # still falls back to ``detect()`` for unknown extensions like ``.txt``
    # — those can be pipe-delimited OR fixed-width and need content
    # inspection to disambiguate.
    _DELIMITED_EXTENSIONS = frozenset({".csv", ".tsv", ".psv"})

    # NDJSON extensions route deterministically to :class:`JsonParser`
    # (ADR 0018). Plain ``.json`` (a top-level array) is deliberately NOT
    # here — it is the unsupported v1 case and is flagged with a clear
    # "convert to NDJSON first" message in :meth:`get_parser_class`.
    _JSON_EXTENSIONS = frozenset({".ndjson", ".jsonl"})

    def get_parser_class(self, file_path: str):
        """Get appropriate parser class for file.

        Routing order:

        1. **Extension first** — ``.csv`` / ``.tsv`` / ``.psv`` route
           directly to :class:`PipeDelimitedParser` (which infers the
           correct delimiter from the extension via
           :func:`PipeDelimitedParser._infer_delimiter`). This is the
           authoritative route for these extensions because the content
           sniffer cannot reliably tell a comma-CSV from a single-column
           pipe file when the row count is small.
        2. **Content sniffing** — for any other extension (chiefly
           ``.txt`` / no extension), the detector inspects sample lines
           to choose between pipe-delimited and fixed-width parsing.

        Args:
            file_path: Path to file

        Returns:
            Parser class (not instance). For delimited formats this is
            always :class:`PipeDelimitedParser`; the per-format separator
            is set by the parser at construction time based on the file's
            extension.

        Raises:
            ValueError: When the file's content cannot be classified and
                the extension is not in the routing table.
        """
        from .pipe_delimited_parser import PipeDelimitedParser
        from .fixed_width_parser import FixedWidthParser
        from .json_parser import JsonParser

        # Extension-driven fast path. Treat known delimited extensions as
        # authoritative — the parser itself maps the extension to the
        # right separator (``.csv`` -> ``,``, ``.tsv`` -> ``\t``,
        # ``.psv`` -> ``|``).
        suffix = os.path.splitext(file_path)[1].lower()
        if suffix in self._DELIMITED_EXTENSIONS:
            return PipeDelimitedParser

        # NDJSON extensions route to JsonParser (ADR 0018). A plain ``.json``
        # holding a top-level array is the unsupported v1 case — fail fast
        # with the one-line conversion recipe rather than a cryptic parse
        # error deep in JsonParser.
        if suffix in self._JSON_EXTENSIONS:
            return JsonParser
        if suffix == ".json" and self._looks_like_json_array(file_path):
            raise ValueError(
                f"{file_path} looks like a top-level JSON array, which is not "
                "supported. Convert it to NDJSON first "
                "(e.g. `jq -c '.[]' in.json > in.ndjson`) and re-run."
            )

        detection = self.detect(file_path)
        format_type = detection['format']

        if format_type == FileFormat.PIPE_DELIMITED:
            return PipeDelimitedParser
        elif format_type == FileFormat.FIXED_WIDTH:
            return FixedWidthParser
        elif format_type == FileFormat.JSON:
            return JsonParser
        elif format_type in (FileFormat.CSV, FileFormat.TSV):
            # Content-sniffed CSV/TSV with an unusual extension — still
            # delegate to PipeDelimitedParser, but in this branch the
            # extension hint is absent so the parser will fall back to
            # the historic pipe default. Callers that need a specific
            # delimiter here should construct PipeDelimitedParser
            # directly with the ``delimiter`` kwarg.
            return PipeDelimitedParser
        else:
            raise ValueError(
                f"Unable to detect file format for {file_path}. "
                f"Confidence: {detection['confidence']:.2f}"
            )
