"""Shared date-format regex table for BA-friendly rule processing.

Mirrors the lowering ``BARulesTemplateConverter._convert_row`` applies for
the legacy ``date format`` rule type (which is rewritten into an ``operator:
regex`` rule at conversion time) so that the runtime ``date_format``
operator handled by :class:`~src.validators.field_validator.FieldValidator`
uses an identical vocabulary.

Adding a new format here makes it available to both:

- ``BARulesTemplateConverter._convert_row`` (compile-time lowering of the
  legacy ``date format`` row type into ``regex`` rules).
- ``FieldValidator.validate_date_format`` (runtime evaluation of native
  ``date_format`` rules emitted by the BA-friendly path).

Keeping the table in one place is required by ADR 0006: the two halves of
the codebase must speak the same date-format vocabulary, and surfacing one
constant prevents drift the next time a format is added.
"""

from __future__ import annotations

from typing import Dict

# Map from BA-friendly format token (upper-cased) to a regex that matches a
# *syntactically* valid value for that format. These regexes intentionally
# err on the side of permissive structure checks — they do not validate
# calendar correctness (e.g. ``^\d{8}$`` accepts ``99999999``). Calendar
# validation, if ever required, belongs in a separate predicate.
DATE_FORMAT_REGEX: Dict[str, str] = {
    "CCYYMMDD": r"^\d{8}$",
    "YYYYMMDD": r"^\d{8}$",
    "MMDDYYYY": r"^\d{8}$",
    "YYYY-MM-DD": r"^\d{4}-\d{2}-\d{2}$",
    "MM/DD/YYYY": r"^\d{2}/\d{2}/\d{4}$",
    "DD/MM/YYYY": r"^\d{2}/\d{2}/\d{4}$",
    "MM/DD/CCYY": r"^\d{2}/\d{2}/\d{4}$",  # TRANERT Batch Header EFF-DAT-BRT
    "DD/MM/CCYY": r"^\d{2}/\d{2}/\d{4}$",
}

# Permissive fallback for unknown format tokens. Matches the historical
# behavior of ``BARulesTemplateConverter._convert_row``: anything that is
# all digits passes. Surfaced as a constant so the fallback is explicit.
DATE_FORMAT_DEFAULT_REGEX: str = r"^\d+$"


def regex_for_format(fmt: str) -> str:
    """Return the regex string for *fmt*, falling back to ``^\\d+$``.

    Args:
        fmt: Format token (e.g. ``"CCYYMMDD"``). Case-insensitive.

    Returns:
        Regex pattern string for the requested format, or
        :data:`DATE_FORMAT_DEFAULT_REGEX` when the format is not recognized.
    """
    return DATE_FORMAT_REGEX.get(fmt.upper(), DATE_FORMAT_DEFAULT_REGEX)
