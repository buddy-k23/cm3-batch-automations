"""Parser for pipe-delimited files."""

import pandas as pd
from pandas.errors import EmptyDataError
from typing import Optional, List
from .base_parser import BaseParser


class PipeDelimitedParser(BaseParser):
    """Parser for pipe-delimited (|) files."""

    def __init__(self, file_path: str, columns: Optional[List[str]] = None):
        """Initialize pipe-delimited parser.
        
        Args:
            file_path: Path to the pipe-delimited file
            columns: Optional list of column names
        """
        super().__init__(file_path)
        self.columns = columns

    def parse(self) -> pd.DataFrame:
        """Parse the pipe-delimited file into a DataFrame.

        Returns:
            DataFrame with columns matching those provided at construction
            (or auto-detected), plus a leading ``__source_row__`` column
            containing the 1-indexed source file line number for each record.
            Because this parser reads with ``header=None``, row 1 in the file
            becomes ``__source_row__ == 1``.

        Raises:
            ValueError: If the file cannot be parsed.
        """
        try:
            df = pd.read_csv(
                self.file_path,
                sep="|",
                names=self.columns,
                header=None,
                dtype=str,
                keep_default_na=False,
            )
            # Insert 1-indexed physical line numbers as the first column.
            # The parser reads with header=None, so data starts at file line 1.
            df.insert(0, '__source_row__', range(1, len(df) + 1))
            return df
        except EmptyDataError:
            return pd.DataFrame(columns=self.columns or [])
        except Exception as e:
            raise ValueError(f"Failed to parse pipe-delimited file: {e}")

    def validate_format(self) -> bool:
        """Validate pipe-delimited format.
        
        Returns:
            True if format is valid
        """
        try:
            with open(self.file_path, "r") as f:
                first_line = f.readline()
                return "|" in first_line
        except Exception:
            return False
