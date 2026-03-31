"""Schema drift detector — detects when a file's layout differs from its mapping.

Fixed-width files are sensitive to layout changes: an upstream producer adding
or removing bytes causes all downstream field positions to silently shift.  This
module implements a heuristic detector that samples the first 20 non-blank
lines of a file and checks whether each field's declared start position begins
with non-whitespace content.  When a field's expected start is consistently
blank but a nearby position has non-blank content, a drift record is emitted.

Delimited files (CSV, pipe, TSV) are checked by comparing the file's actual
column headers (or column count when no header is present) against the field
names declared in the mapping.
"""

from __future__ import annotations

import os
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAMPLE_SIZE = 20
_MIN_SAMPLE_LINES = 3
_LEADING_BLANK_THRESHOLD = 0.8   # >80% of lines have blank at expected start → suspect drift
_CONTENT_RATIO_THRESHOLD = 0.5   # >50% of lines must have boundary-start to accept position
_SEARCH_RADIUS = 15              # bytes left/right of expected position to scan
_SEARCH_MAX_POS = 200            # upper bound for position scan
_LARGE_OFFSET_THRESHOLD = 5     # offsets larger than this → severity='error'


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _detect_fixed_width_drift(lines: list[str], mapping: dict[str, Any]) -> dict[str, Any]:
    """Detect position/width drift in a fixed-width file.

    Samples the first ``_SAMPLE_SIZE`` non-blank lines and checks whether each
    field's declared byte position begins with non-whitespace.  When more than
    80 % of sample lines have whitespace at the declared start byte and a
    nearby position has a clear content boundary in more than 50 % of lines,
    the field is reported as drifted.

    Args:
        lines: List of text lines from the file (newlines may be present but
            are not stripped — callers may pass raw ``file.readlines()`` output).
        mapping: Mapping dict with a ``'fields'`` list.  Each field entry must
            have ``'name'`` and ``'length'`` keys.  ``'position'`` is a 1-indexed
            byte offset; when absent, the field is skipped.

    Returns:
        On success::

            {'drifted': bool, 'fields': list[dict]}

        Each entry in ``fields`` has keys:
            ``name``, ``expected_start`` (1-indexed), ``expected_length``,
            ``actual_start`` (1-indexed), ``actual_length``, ``severity``
            (``'warning'`` for offset <= 5, ``'error'`` for offset > 5).

        On early exit::

            {'drifted': False, 'fields': [], 'skipped': True, 'reason': str}

        Possible ``reason`` values: ``'too_short'``, ``'no_fields'``.
    """
    sample = [line for line in lines if line.strip()][:_SAMPLE_SIZE]

    if len(sample) < _MIN_SAMPLE_LINES:
        return {"drifted": False, "fields": [], "skipped": True, "reason": "too_short"}

    fields = mapping.get("fields", [])
    if not fields:
        return {"drifted": False, "fields": [], "skipped": True, "reason": "no_fields"}

    drifted_fields: list[dict[str, Any]] = []

    for field in fields:
        name = field.get("name", "")
        position = field.get("position")  # 1-indexed byte offset; may be absent
        length = field.get("length", 0)

        if not length:
            continue

        # When position is absent the field carries no positional information
        # and cannot be drift-checked.
        if position is None:
            continue

        begin = int(position) - 1  # convert to 0-indexed
        length_int = int(length)

        # Drift signal: count lines where the expected start byte is blank.
        # A consistently blank start byte means content did not begin here.
        leading_blank_count = sum(
            1
            for line in sample
            if len(line) <= begin or not line[begin : begin + 1].strip()
        )
        leading_blank_ratio = leading_blank_count / len(sample)

        if leading_blank_ratio > _LEADING_BLANK_THRESHOLD:
            actual_begin = _find_actual_position(sample, begin, length_int)
            if actual_begin is not None and actual_begin != begin:
                offset = abs(actual_begin - begin)
                severity = "error" if offset > _LARGE_OFFSET_THRESHOLD else "warning"
                drifted_fields.append(
                    {
                        "name": name,
                        "expected_start": int(position),       # 1-indexed
                        "expected_length": length_int,
                        "actual_start": actual_begin + 1,      # back to 1-indexed
                        "actual_length": length_int,
                        "severity": severity,
                    }
                )

    return {
        "drifted": len(drifted_fields) > 0,
        "fields": drifted_fields,
    }


def _find_actual_position(
    sample: list[str],
    expected_begin: int,
    length: int,
) -> Optional[int]:
    """Scan nearby byte positions to find where content actually begins.

    Uses a "boundary-start" heuristic: a position scores a point for each
    sample line where that byte is non-whitespace AND the preceding byte is
    whitespace (or it is position 0).  This identifies where a field's content
    starts rather than where it merely overlaps.

    Searches within ``_SEARCH_RADIUS`` bytes on either side of ``expected_begin``
    (excluding ``expected_begin`` itself) for the position whose boundary-start
    score exceeds 50 % of sample lines.

    Args:
        sample: Non-blank lines to inspect (already filtered).
        expected_begin: 0-indexed byte offset that is known to be mostly blank.
        length: Field byte length (used only to bound the line-length check).

    Returns:
        The 0-indexed byte offset of the best candidate position, or ``None``
        when no candidate exceeds the 50 % boundary-start threshold.
    """
    low = max(0, expected_begin - _SEARCH_RADIUS)
    high = min(_SEARCH_MAX_POS, expected_begin + _SEARCH_RADIUS)

    best_pos: Optional[int] = None
    best_score = 0

    for pos in range(low, high):
        if pos == expected_begin:
            continue

        boundary_start_count = sum(
            1
            for line in sample
            if len(line) >= pos + 1
            and line[pos : pos + 1].strip()                   # current byte is non-blank
            and (pos == 0 or not line[pos - 1 : pos].strip()) # preceding byte is blank (or start)
        )

        if boundary_start_count > best_score:
            best_score = boundary_start_count
            best_pos = pos

    threshold = len(sample) * _CONTENT_RATIO_THRESHOLD
    return best_pos if best_score > threshold else None


# ---------------------------------------------------------------------------
# Delimited drift detection
# ---------------------------------------------------------------------------


def _detect_delimited_drift(
    lines: list[str],
    mapping: dict[str, Any],
    delimiter: str,
) -> dict[str, Any]:
    """Detect column drift in a delimited file.

    Compares the file's actual headers or column count against the field names
    declared in the mapping.  When the first line appears to be a header row
    (contains at least one non-numeric token), columns are matched by name.
    Otherwise the check falls back to comparing the total column count.

    Args:
        lines: File lines (raw ``readlines()`` output is accepted).
        mapping: Mapping dict with a ``'fields'`` list.  Each field entry should
            have a ``'name'`` key.
        delimiter: Column separator character (e.g. ``','``, ``'|'``, ``'\\t'``).

    Returns:
        On success::

            {'drifted': bool, 'fields': list[dict]}

        Each entry in ``fields`` has keys:
            ``name``, ``expected_start`` (``None`` for delimited),
            ``expected_length`` (``None``), ``actual_start`` (``None``),
            ``actual_length`` (``None``), ``severity``, ``reason``.

        ``reason`` values:
            ``'column_missing'`` (severity ``'error'``) — a mapped field name
            is absent from the header row.
            ``'unexpected_column'`` (severity ``'warning'``) — a column in the
            file is not declared in the mapping.
            ``'column_count_mismatch'`` (severity ``'error'``) — no header row
            and the column count differs; ``expected_start`` holds the expected
            count, ``actual_start`` holds the actual count.

        On early exit::

            {'drifted': False, 'fields': [], 'skipped': True, 'reason': str}

        Possible ``reason`` values: ``'too_short'``, ``'no_fields'``.
    """
    sample = [line for line in lines if line.strip()]
    if len(sample) < 1:
        return {"drifted": False, "fields": [], "skipped": True, "reason": "too_short"}

    expected_fields = [f.get("name", "") for f in mapping.get("fields", [])]
    if not expected_fields:
        return {"drifted": False, "fields": [], "skipped": True, "reason": "no_fields"}

    first_line_cols = sample[0].split(delimiter)

    # Detect whether the first row is a header by checking for non-numeric tokens.
    has_header = any(
        not col.strip().lstrip("-").replace(".", "").isdigit()
        for col in first_line_cols
        if col.strip()
    )

    drifted_fields: list[dict[str, Any]] = []

    if has_header:
        actual_names = [c.strip() for c in first_line_cols]
        # Missing expected columns → error
        for field_name in expected_fields:
            if field_name not in actual_names:
                drifted_fields.append(
                    {
                        "name": field_name,
                        "expected_start": None,
                        "expected_length": None,
                        "actual_start": None,
                        "actual_length": None,
                        "severity": "error",
                        "reason": "column_missing",
                    }
                )
        # Extra unexpected columns → warning
        for actual_name in actual_names:
            if actual_name and actual_name not in expected_fields:
                drifted_fields.append(
                    {
                        "name": actual_name,
                        "expected_start": None,
                        "expected_length": None,
                        "actual_start": None,
                        "actual_length": None,
                        "severity": "warning",
                        "reason": "unexpected_column",
                    }
                )
    else:
        # No header — compare total column count.
        actual_count = len(first_line_cols)
        expected_count = len(expected_fields)
        if actual_count != expected_count:
            drifted_fields.append(
                {
                    "name": "_column_count",
                    "expected_start": expected_count,
                    "expected_length": None,
                    "actual_start": actual_count,
                    "actual_length": None,
                    "severity": "error",
                    "reason": "column_count_mismatch",
                }
            )

    return {"drifted": len(drifted_fields) > 0, "fields": drifted_fields}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def detect_drift(file_path: str, mapping: dict[str, Any]) -> dict[str, Any]:
    """Detect whether a file's layout has drifted from its mapping.

    Reads the file at ``file_path`` and delegates to the appropriate
    sub-detector based on the ``'format'`` (or ``'file_format'``) key in the
    mapping dict.

    Supported format values:

    * ``'csv'`` → comma delimiter
    * ``'pipe'``, ``'pipe-delimited'``, ``'psv'`` → pipe delimiter
    * ``'tsv'``, ``'tab'`` → tab delimiter
    * ``'fixed'``, ``'fixed-width'``, ``'fixed_width'``, ``''`` (empty/absent)
      → fixed-width heuristic detector

    Args:
        file_path: Absolute or relative path to the data file.
        mapping: Mapping config dict.  Must contain a ``'fields'`` list and
            optionally a ``'format'`` or ``'file_format'`` key.

    Returns:
        Drift report dict with at minimum ``'drifted'`` (bool) and
        ``'fields'`` (list).  May include ``'skipped'`` and ``'reason'`` keys
        when the check cannot be performed.

        Possible ``reason`` values for skipped results:
            ``'file_not_found'``, ``'read_error'``, ``'unsupported_format'``,
            plus reasons propagated from the sub-detectors.
    """
    if not os.path.exists(file_path):
        return {
            "drifted": False,
            "fields": [],
            "skipped": True,
            "reason": "file_not_found",
        }

    try:
        with open(file_path, "r", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return {
            "drifted": False,
            "fields": [],
            "skipped": True,
            "reason": "read_error",
        }

    fmt = (mapping.get("format") or mapping.get("file_format") or "").lower()

    if fmt in ("csv",):
        return _detect_delimited_drift(lines, mapping, ",")
    elif fmt in ("pipe", "pipe-delimited", "psv"):
        return _detect_delimited_drift(lines, mapping, "|")
    elif fmt in ("tsv", "tab"):
        return _detect_delimited_drift(lines, mapping, "\t")
    elif fmt in ("fixed", "fixed-width", "fixed_width", ""):
        return _detect_fixed_width_drift(lines, mapping)
    else:
        return {
            "drifted": False,
            "fields": [],
            "skipped": True,
            "reason": "unsupported_format",
        }
