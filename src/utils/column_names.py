"""Shared column-name normalization helpers.

Different sources spell the "same" column differently: mapping field names use
hyphens and arbitrary case (e.g. ``ACCT-NUM``), while raw Oracle cursor column
names are upper-cased with underscores (e.g. ``ACCT_NUM``) because hyphens are
illegal SQL identifiers.  When two such sides are merged on a key column the
names never line up.

The canonical form chosen here is **UPPER case with underscores**, matching the
Oracle side, because Oracle column names cannot be changed at the source.
"""
from __future__ import annotations

from typing import Iterable, List


def normalize_column_name(name: str) -> str:
    """Normalize a single column name to the canonical UPPER+underscore form.

    Strips surrounding whitespace, upper-cases, and replaces hyphens with
    underscores so that mapping field names (``ACCT-NUM``) and raw Oracle
    cursor names (``ACCT_NUM``) collapse to the same identifier (``ACCT_NUM``).

    Args:
        name: The column name to normalize. Non-string inputs are coerced via
            ``str`` first so callers can pass, e.g., pandas column labels.

    Returns:
        The normalized column name in UPPER case with hyphens replaced by
        underscores and surrounding whitespace removed.
    """
    return str(name).strip().upper().replace("-", "_")


def normalize_column_names(names: Iterable[str]) -> List[str]:
    """Normalize an iterable of column names.

    Args:
        names: Column names to normalize (e.g. a DataFrame's ``.columns`` or a
            list of key-column names).

    Returns:
        A list of normalized column names, in the original order.
    """
    return [normalize_column_name(n) for n in names]
