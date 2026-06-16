"""Parser for delimited files (pipe / comma / tab)."""

import os
import pandas as pd
from pandas.errors import EmptyDataError
from typing import Optional, List
from .base_parser import BaseParser


# Map of file extensions to their canonical single-character delimiters.
# Used only when ``PipeDelimitedParser`` is constructed without an explicit
# ``delimiter`` so we can fall back to the right separator instead of
# silently mis-parsing CSV/TSV files (S8-2, #393).
_EXTENSION_DELIMITERS = {
    ".csv": ",",
    ".tsv": "\t",
    ".psv": "|",
    ".txt": "|",
}


class PipeDelimitedParser(BaseParser):
    """Parser for delimited files.

    Despite the legacy class name, this parser handles any single-character
    delimited format. The ``delimiter`` argument controls the separator;
    when omitted it is inferred from the file extension (``.csv`` -> ``,``,
    ``.tsv`` -> tab, ``.psv`` / ``.txt`` -> ``|``). Unknown extensions fall
    back to the historic pipe (``|``) default so legacy call sites that
    pass un-extensioned paths keep working.
    """

    def __init__(
        self,
        file_path: str,
        columns: Optional[List[str]] = None,
        delimiter: Optional[str] = None,
    ):
        """Initialize delimited-file parser.

        Args:
            file_path: Path to the delimited file.
            columns: Optional list of column names. When omitted the
                parser reads with ``header=None`` and emits integer-indexed
                columns (preserving the historic behaviour callers rely on).
            delimiter: Optional single-character field separator. When
                ``None``, the parser infers it from the file extension —
                ``.csv`` -> ``,``, ``.tsv`` -> ``\\t``, ``.psv`` / ``.txt``
                or any other extension -> ``|``. Pass an explicit value to
                override extension-based inference.
        """
        super().__init__(file_path)
        self.columns = columns
        self.delimiter = delimiter if delimiter is not None else self._infer_delimiter(file_path)

    @staticmethod
    def _infer_delimiter(file_path: str) -> str:
        """Map a file extension to its canonical delimiter.

        Args:
            file_path: Path whose extension is consulted (case-insensitive).

        Returns:
            ``","`` for ``.csv``, ``"\\t"`` for ``.tsv``, ``"|"`` for
            ``.psv`` / ``.txt`` / any other extension. Pipe is the
            backwards-compatible fallback so callers that built this
            parser with an extension-less path keep their historic
            pipe-delimited behaviour.
        """
        suffix = os.path.splitext(file_path)[1].lower()
        return _EXTENSION_DELIMITERS.get(suffix, "|")

    def parse(self) -> pd.DataFrame:
        """Parse the delimited file into a DataFrame.

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
                sep=self.delimiter,
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
            raise ValueError(f"Failed to parse delimited file: {e}")

    def validate_format(self) -> bool:
        """Validate that the file's first line contains the active delimiter.

        Returns:
            True if the configured ``delimiter`` appears in the first line
            of the file. Returns False if the file cannot be opened or the
            delimiter is absent.
        """
        try:
            with open(self.file_path, "r") as f:
                first_line = f.readline()
                return self.delimiter in first_line
        except Exception:
            return False
