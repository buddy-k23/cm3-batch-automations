"""String / value utilities shared across the SHAW CSV-generation pipeline.

Pulled verbatim from the original ``generate_shaw_atoctran_csvs.py`` so
the ATOCTRAN behavior is preserved bit-for-bit while making the helpers
reusable by future per-source wrappers (TRANERT, CONTACT, …).
"""

from __future__ import annotations

import re
from typing import Any


def to_str(v: Any) -> str:
    """Return *v* as a stripped string, or ``""`` for ``None``."""
    if v is None:
        return ""
    return str(v).strip()


def int_or_blank(v: Any) -> str:
    """Coerce a cell value to its integer string form, or ``""`` if blank.

    Workbook cells often arrive as floats (``13.0``) where the spec
    intends ints (``13``). This collapses that without losing genuinely
    non-numeric content.
    """
    if v is None or v == "":
        return ""
    try:
        return str(int(float(v)))
    except (ValueError, TypeError):
        return to_str(v)


def flatten(text: str) -> str:
    """Collapse all internal whitespace to single spaces."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, n: int) -> str:
    """Truncate *text* to *n* chars (after :func:`flatten`) with ``...`` suffix."""
    if not text:
        return ""
    flat = flatten(text)
    return flat if len(flat) <= n else flat[: n - 3] + "..."


def to_snake(name: str) -> str:
    """Lowercase + underscore-separated identifier (Target Name default)."""
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def is_required(raw: Any) -> bool:
    """Recognize common BA spellings of "required"."""
    s = to_str(raw).upper()
    return s in {"Y", "YES", "TRUE", "1", "REQUIRED"}


def normalize_data_type(raw_datatype: str, raw_format: str) -> str:
    """Map workbook Datatype + Format to Valdo CSV Data Type.

    Args:
        raw_datatype: Value from the workbook's Datatype column.
        raw_format: Value from the workbook's Format column.

    Returns:
        One of ``"Date"``, ``"Numeric"``, ``"String"``, or a title-cased
        fallback when the workbook used an unrecognized token.
    """
    dt = raw_datatype.lower()
    fmt = raw_format.lower()
    if "date" in dt or "ccyymmdd" in fmt or "mm/dd" in fmt or "yyyymmdd" in fmt:
        return "Date"
    if dt.startswith("num") or fmt.startswith("9(") or fmt.startswith("+9("):
        return "Numeric"
    if dt.startswith("str") or dt.startswith("alpha") or fmt.startswith("x("):
        return "String"
    if not dt and not fmt:
        return "String"
    return dt.title() if dt else "String"


def derive_length(raw_length: str, raw_format: str) -> str:
    """Best-effort recovery of the field's character length.

    Prefers the explicit ``Length`` cell. Falls back to parsing the
    ``Format`` cell when ``Length`` is blank — e.g. ``9(5)`` → ``5``,
    ``X(18)`` → ``18``, ``+9(12)V9(6)`` → ``19`` (sign included).

    Returns the empty string when neither source yields a length.
    """
    if raw_length:
        try:
            return str(int(float(raw_length)))
        except (ValueError, TypeError):
            pass

    fmt = raw_format.replace(" ", "")
    parens = re.findall(r"\((\d+)\)", fmt)
    if parens:
        total = sum(int(p) for p in parens)
        if "+" in fmt or "S9" in fmt.upper():
            total += 1
        occur_match = re.search(r"(\d+)occur", fmt, re.IGNORECASE)
        if occur_match:
            total *= int(occur_match.group(1))
        return str(total)
    return ""
