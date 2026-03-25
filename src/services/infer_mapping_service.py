"""Service for inferring mapping drafts from sample data files."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


# Date patterns: YYYYMMDD or CCYYMMDD (8 digits starting with 19/20)
_DATE_RE = re.compile(r"^(?:19|20)\d{6}$")

# Numeric: optional sign, digits, optional decimal
_NUMERIC_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")


def _infer_type(values: List[str]) -> str:
    """Infer the data type from a list of sample values.

    Args:
        values: Non-empty stripped sample values for a single field.

    Returns:
        One of ``"date"``, ``"number"``, or ``"string"``.
    """
    if not values:
        return "string"

    non_empty = [v for v in values if v.strip()]
    if not non_empty:
        return "string"

    # Check date first (all non-empty must match)
    if all(_DATE_RE.match(v) for v in non_empty):
        return "date"

    # Check numeric
    if all(_NUMERIC_RE.match(v) for v in non_empty):
        return "number"

    return "string"


def _detect_fixed_width_boundaries(lines: List[str]) -> List[tuple[int, int]]:
    """Detect field boundaries in fixed-width data by finding consistent space columns.

    The algorithm looks at every character position across all sample lines and
    identifies columns where *every* line has a space character.  Contiguous runs
    of such space-only columns are treated as field separators; the regions
    between them become fields.

    As a fallback (when no universal-space columns exist), the entire record
    width is returned as a single field.

    Args:
        lines: Sample lines (already stripped of trailing newline).

    Returns:
        List of ``(start, end)`` position tuples.
    """
    if not lines:
        return []

    max_len = max(len(line) for line in lines)
    if max_len == 0:
        return []

    # Pad all lines to max_len so indexing is safe.
    padded = [line.ljust(max_len) for line in lines]

    # Find positions where every line has a space.
    space_mask = [True] * max_len
    for line in padded:
        for i, ch in enumerate(line):
            if ch != " ":
                space_mask[i] = False

    # Build field spans from non-space regions.
    fields: List[tuple[int, int]] = []
    in_field = False
    start = 0
    for i in range(max_len):
        if not space_mask[i]:
            if not in_field:
                start = i
                in_field = True
        else:
            if in_field:
                fields.append((start, i))
                in_field = False
    if in_field:
        fields.append((start, max_len))

    # If no boundaries detected, return the whole line as one field.
    if not fields:
        fields = [(0, max_len)]

    return fields


def _count_delimited_columns(lines: List[str], delimiter: str) -> int:
    """Return the most common column count across sample lines.

    Args:
        lines: Sample lines.
        delimiter: The field delimiter character.

    Returns:
        Most frequent column count (minimum 1).
    """
    if not lines:
        return 1

    counts: Dict[int, int] = {}
    for line in lines:
        n = len(line.split(delimiter))
        counts[n] = counts.get(n, 0) + 1

    return max(counts, key=counts.get)  # type: ignore[arg-type]


def _read_sample_lines(file_path: str, sample_lines: int) -> List[str]:
    """Read up to *sample_lines* lines from *file_path*.

    Args:
        file_path: Path to the data file.
        sample_lines: Maximum number of lines to read.

    Returns:
        List of lines with trailing newlines stripped.
    """
    result: List[str] = []
    with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
        for i, raw_line in enumerate(fh):
            if i >= sample_lines:
                break
            result.append(raw_line.rstrip("\n"))
    return result


def _format_string_to_delimiter(fmt: str) -> Optional[str]:
    """Map a format name to the corresponding delimiter character.

    Args:
        fmt: One of ``"pipe_delimited"``, ``"csv"``, ``"tsv"``.

    Returns:
        Delimiter character, or ``None`` for fixed-width / unknown.
    """
    return {
        "pipe_delimited": "|",
        "csv": ",",
        "tsv": "\t",
    }.get(fmt)


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def infer_mapping(
    file_path: str,
    format: Optional[str] = None,
    sample_lines: int = 100,
) -> Dict[str, Any]:
    """Infer a draft mapping configuration from a sample data file.

    The function auto-detects the file format (unless *format* is specified),
    analyses sample rows to discover field boundaries (fixed-width) or column
    counts (delimited), infers data types, and generates a mapping JSON dict
    compatible with the project's universal mapping schema.

    Args:
        file_path: Path to the sample data file.
        format: Optional format override.  One of ``"fixed_width"``,
            ``"pipe_delimited"``, ``"csv"``, or ``"tsv"``.
            When ``None`` the format is auto-detected via
            :class:`~src.parsers.format_detector.FormatDetector`.
        sample_lines: Number of lines to read for analysis (default 100).

    Returns:
        A mapping ``dict`` with the standard keys (``mapping_name``,
        ``version``, ``source``, ``fields``, ``metadata``) plus
        ``_inferred: true`` and ``_note: "DRAFT"`` markers.

    Raises:
        FileNotFoundError: If *file_path* does not exist.
        ValueError: If the format cannot be determined.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    lines = _read_sample_lines(file_path, sample_lines)
    if not lines:
        raise ValueError(f"File is empty: {file_path}")

    # ---- Determine format ------------------------------------------------
    detected_format = format
    if detected_format is None:
        from src.parsers.format_detector import FormatDetector

        detector = FormatDetector()
        result = detector.detect(file_path)
        detected_format = result["format"].value
        if detected_format == "unknown":
            raise ValueError(
                f"Unable to auto-detect format for {file_path}. "
                f"Confidence: {result['confidence']:.2f}. "
                "Use --format to specify explicitly."
            )

    # ---- Build fields ----------------------------------------------------
    fields: List[Dict[str, Any]] = []

    if detected_format == "fixed_width":
        boundaries = _detect_fixed_width_boundaries(lines)
        for idx, (start, end) in enumerate(boundaries):
            col_values = [line[start:end].strip() for line in lines if len(line) >= start]
            inferred_type = _infer_type(col_values)
            fields.append({
                "name": f"FIELD_{idx + 1:03d}",
                "data_type": inferred_type,
                "position": start,
                "length": end - start,
                "required": False,
                "description": f"Auto-inferred field at positions {start}-{end}",
                "transformations": [{"type": "trim"}],
                "validation_rules": [],
            })
    else:
        # Delimited format
        delimiter = _format_string_to_delimiter(detected_format)
        if delimiter is None:
            raise ValueError(f"Unsupported format: {detected_format}")

        num_cols = _count_delimited_columns(lines, delimiter)
        # Collect sample values per column
        for col_idx in range(num_cols):
            col_values = []
            for line in lines:
                parts = line.split(delimiter)
                if col_idx < len(parts):
                    col_values.append(parts[col_idx].strip())
            inferred_type = _infer_type(col_values)
            fields.append({
                "name": f"FIELD_{col_idx + 1:03d}",
                "data_type": inferred_type,
                "required": False,
                "description": f"Auto-inferred column {col_idx + 1}",
                "transformations": [{"type": "trim"}],
                "validation_rules": [],
            })

    # ---- Assemble mapping dict -------------------------------------------
    source_block: Dict[str, Any] = {
        "type": "file",
        "format": detected_format,
        "encoding": "UTF-8",
    }
    if detected_format != "fixed_width":
        delimiter = _format_string_to_delimiter(detected_format)
        if delimiter is not None:
            source_block["delimiter"] = delimiter

    now_iso = datetime.utcnow().isoformat() + "Z"

    mapping: Dict[str, Any] = {
        "mapping_name": f"{path.stem}_inferred",
        "version": "0.1.0",
        "description": f"DRAFT mapping inferred from {path.name}",
        "_inferred": True,
        "_note": "DRAFT",
        "source": source_block,
        "target": {"type": "database"},
        "fields": fields,
        "key_columns": [],
        "metadata": {
            "created_by": "infer_mapping",
            "created_date": now_iso,
            "last_modified": now_iso,
            "source_file": str(path.name),
            "sample_lines_analyzed": min(sample_lines, len(lines)),
        },
    }

    return mapping
