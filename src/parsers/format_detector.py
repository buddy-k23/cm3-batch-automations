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

    def get_parser_class(self, file_path: str):
        """Get appropriate parser class for file.
        
        Args:
            file_path: Path to file
            
        Returns:
            Parser class (not instance)
        """
        from .pipe_delimited_parser import PipeDelimitedParser
        from .fixed_width_parser import FixedWidthParser

        detection = self.detect(file_path)
        format_type = detection['format']

        if format_type == FileFormat.PIPE_DELIMITED:
            return PipeDelimitedParser
        elif format_type == FileFormat.FIXED_WIDTH:
            return FixedWidthParser
        elif format_type in (FileFormat.CSV, FileFormat.TSV):
            # Can use pipe delimited parser with different delimiter
            return PipeDelimitedParser
        else:
            raise ValueError(
                f"Unable to detect file format for {file_path}. "
                f"Confidence: {detection['confidence']:.2f}"
            )
