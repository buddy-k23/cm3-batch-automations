"""Service for extracting failed rows from a validated batch file.

Issue #227 — provides :func:`extract_error_rows` used by both the CLI
``--export-errors`` flag and the ``POST /api/v1/files/export-errors`` endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


# Delimiters tried in priority order when auto-detecting file format.
_DELIMITER_CANDIDATES: tuple[str, ...] = ("|", "\t", ",")


def _detect_delimiter(file_path: str) -> str | None:
    """Detect the field delimiter of a delimited file by inspecting the first line.

    Tries pipe ``|``, tab ``\\t``, then comma ``,`` in that order.  Returns
    ``None`` when none of the candidates are found (i.e. the file is treated as
    fixed-width).

    Args:
        file_path: Path to the data file to inspect.

    Returns:
        The delimiter character string, or ``None`` for fixed-width files.
    """
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            first_line = fh.readline()
        for delim in _DELIMITER_CANDIDATES:
            if delim in first_line:
                return delim
    except OSError:
        pass
    return None


def extract_error_rows(
    file_path: str,
    validation_result: dict[str, Any],
    output_path: str,
) -> dict[str, Any]:
    """Extract failed rows from the original file and write them to output_path.

    Collects the unique set of 1-indexed row numbers referenced in
    ``validation_result['errors']``, reads those lines from ``file_path``,
    and writes them to ``output_path``.

    For **delimited** files (pipe, tab, or comma-separated) the header row
    (row 1) is always written first so the output file is self-describing.
    For **fixed-width** files only the raw failed lines are written.

    When there are no errors the output file is still created:
    - Fixed-width: 0 bytes.
    - Delimited: header row only.

    Handles chunked validation results — errors from all chunks reference
    absolute 1-indexed row numbers, so no special handling is needed here.

    Args:
        file_path: Path to the original batch file.
        validation_result: Dict with an ``'errors'`` key; each error entry
            must have a ``'row'`` field (1-indexed integer).  Missing or
            ``None`` row values are silently skipped.
        output_path: Path to write the extracted rows to.  Parent directory
            is created automatically if it does not exist.

    Returns:
        Dict with keys:

        - ``exported_rows`` (int): number of unique failed rows written.
        - ``output_path`` (str): the value passed in as ``output_path``.

    Raises:
        OSError: If ``file_path`` cannot be read or ``output_path`` cannot
            be written.
    """
    # Collect unique failed row numbers (1-indexed).
    error_rows: set[int] = set()
    for err in validation_result.get("errors", []):
        row = err.get("row") if isinstance(err, dict) else None
        if isinstance(row, int):
            error_rows.add(row)

    # Ensure parent directory exists.
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    delimiter = _detect_delimiter(file_path)
    is_delimited = delimiter is not None

    # Read every line from the source file into memory (indexed from 1).
    with open(file_path, encoding="utf-8", errors="replace") as fh:
        all_lines: list[str] = fh.readlines()

    # For delimited files, row 1 is the header.
    header_line: str | None = all_lines[0].rstrip("\n") if (is_delimited and all_lines) else None

    with open(output_path, "w", encoding="utf-8") as out_fh:
        if is_delimited and header_line is not None:
            # Always write the header for delimited files.
            out_fh.write(header_line + "\n")

        if not error_rows:
            # No failed rows — file is header-only (delimited) or empty (fixed).
            return {"exported_rows": 0, "output_path": output_path}

        # Write failed rows in file order.
        exported = 0
        for row_num in sorted(error_rows):
            line_idx = row_num - 1  # convert 1-indexed to 0-indexed
            if 0 <= line_idx < len(all_lines):
                out_fh.write(all_lines[line_idx].rstrip("\n") + "\n")
                exported += 1

    return {"exported_rows": exported, "output_path": output_path}
