"""Parser for fixed-width files."""

import pandas as pd
from typing import List, Tuple, Dict, Any
from .base_parser import BaseParser


class FixedWidthParser(BaseParser):
    """Parser for fixed-width format files."""

    def __init__(self, file_path: str, column_specs: List[Tuple[str, int, int]]):
        """Initialize fixed-width parser.
        
        Args:
            file_path: Path to the fixed-width file
            column_specs: List of tuples (column_name, start_pos, end_pos)
        """
        super().__init__(file_path)
        self.column_specs = column_specs

    def parse(self) -> pd.DataFrame:
        """Parse the fixed-width file into a DataFrame.

        Returns:
            DataFrame with columns matching the mapping definition,
            plus a leading ``__source_row__`` column containing the
            1-indexed source file line number for each record.

        Raises:
            ValueError: If the file cannot be parsed.
        """
        try:
            colspecs = [(start, end) for _, start, end in self.column_specs]
            names = [name for name, _, _ in self.column_specs]

            df = pd.read_fwf(
                self.file_path,
                colspecs=colspecs,
                names=names,
                dtype=str,
            )
            # Insert 1-indexed physical line numbers as the first column.
            # Fixed-width files have exactly one record per line, so the
            # DataFrame row index maps directly to the source file line number.
            df.insert(0, '__source_row__', range(1, len(df) + 1))
            return df
        except Exception as e:
            raise ValueError(f"Failed to parse fixed-width file: {e}")

    def get_expected_record_length(self) -> int:
        """Get expected record length from column specs."""
        if not self.column_specs:
            return 0
        return max(end for _, _, end in self.column_specs)

    def analyze_line_lengths(self, sample_size: int = 1000) -> Dict[str, Any]:
        """Analyze line lengths and return mismatch details.

        Args:
            sample_size: Maximum number of line-level mismatches to keep in detail

        Returns:
            Dict containing expected length, mismatches, and summary counts.
        """
        expected_length = self.get_expected_record_length()
        mismatches = []
        total_lines = 0
        mismatch_count = 0

        with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
            for line_no, line in enumerate(f, start=1):
                total_lines += 1
                actual = len(line.rstrip("\n"))
                if actual != expected_length:
                    mismatch_count += 1
                    if len(mismatches) < sample_size:
                        mismatches.append({
                            "line_number": line_no,
                            "actual_length": actual,
                            "expected_length": expected_length,
                        })

        return {
            "expected_length": expected_length,
            "total_lines": total_lines,
            "mismatch_count": mismatch_count,
            "mismatches": mismatches,
        }

    def validate_format(self) -> bool:
        """Validate fixed-width format.
        
        Returns:
            True if format is valid
        """
        try:
            with open(self.file_path, "r", encoding="utf-8", errors="replace") as f:
                lengths = [len(line.rstrip("\n")) for line in f]
            return bool(lengths) and len(set(lengths)) == 1
        except Exception:
            return False
