"""Field-level validation for business rules."""

import pandas as pd
import re
from typing import Any, List

from src.validators._date_formats import regex_for_format


class FieldValidator:
    """Validate individual field values against rules."""
    
    def validate_numeric(self, df: pd.DataFrame, field: str, 
                        operator: str, value: Any) -> pd.Series:
        """
        Validate numeric field against value.
        
        Args:
            df: DataFrame
            field: Field name
            operator: Comparison operator (>, <, >=, <=, ==, !=)
            value: Value to compare against
            
        Returns:
            Boolean mask where True indicates violation
        """
        series = pd.to_numeric(df[field], errors='coerce')
        
        if operator == '>':
            return ~(series > value)
        elif operator == '<':
            return ~(series < value)
        elif operator == '>=':
            return ~(series >= value)
        elif operator == '<=':
            return ~(series <= value)
        elif operator == '==':
            return ~(series == value)
        elif operator == '!=':
            return ~(series != value)
        else:
            raise ValueError(f"Unknown numeric operator: {operator}")
    
    def validate_list(self, df: pd.DataFrame, field: str,
                     operator: str, values: List[Any]) -> pd.Series:
        """Validate field value is in/not in a list of allowed values.

        Both the field values and each entry in *values* are stripped of
        leading/trailing whitespace before comparison. This ensures fixed-width
        padded values (e.g. ``'LS  '``) match trimmed valid-value lists
        (e.g. ``['LS']``).

        Args:
            df: DataFrame containing the field to validate.
            field: Column name to validate.
            operator: ``'in'`` to flag values absent from the list, or
                ``'not_in'`` to flag values present in the list.
            values: Allowed (or excluded) value list. Each entry is stripped
                before comparison.

        Returns:
            Boolean Series where ``True`` indicates a violation.

        Raises:
            ValueError: When *operator* is not ``'in'`` or ``'not_in'``.
        """
        series = df[field].astype(str).str.strip()
        stripped_values = [str(v).strip() for v in values]

        if operator == 'in':
            return ~series.isin(stripped_values)
        elif operator == 'not_in':
            return series.isin(stripped_values)
        else:
            raise ValueError(f"Unknown list operator: {operator}")
    
    def validate_regex(self, df: pd.DataFrame, field: str, 
                      pattern: str) -> pd.Series:
        """
        Validate field matches regex pattern.
        
        Args:
            df: DataFrame
            field: Field name
            pattern: Regex pattern
            
        Returns:
            Boolean mask where True indicates violation
        """
        series = df[field].astype(str).str.strip()
        
        # Return True for values that DON'T match pattern (violations)
        return ~series.str.match(pattern, na=False)
    
    def validate_range(self, df: pd.DataFrame, field: str, 
                      min_val: Any, max_val: Any) -> pd.Series:
        """
        Validate field is within range.
        
        Args:
            df: DataFrame
            field: Field name
            min_val: Minimum value (inclusive)
            max_val: Maximum value (inclusive)
            
        Returns:
            Boolean mask where True indicates violation
        """
        series = pd.to_numeric(df[field], errors='coerce')
        
        # Violation if value is outside range or NaN
        return ~((series >= min_val) & (series <= max_val))
    
    def validate_not_null(self, df: pd.DataFrame, field: str) -> pd.Series:
        """
        Validate field is not null/empty.
        
        Args:
            df: DataFrame
            field: Field name
            
        Returns:
            Boolean mask where True indicates violation
        """
        series = df[field]
        
        # Check for null, empty string, or whitespace-only
        is_null = series.isna()
        is_empty = series.astype(str).str.strip() == ''
        
        return is_null | is_empty
    
    def validate_length(self, df: pd.DataFrame, field: str, 
                       min_length: int = None, max_length: int = None) -> pd.Series:
        """
        Validate string length.
        
        Args:
            df: DataFrame
            field: Field name
            min_length: Minimum length (optional)
            max_length: Maximum length (optional)
            
        Returns:
            Boolean mask where True indicates violation
        """
        series = df[field].astype(str)
        lengths = series.str.len()
        
        violations = pd.Series([False] * len(df), index=df.index)
        
        if min_length is not None:
            violations |= lengths < min_length
        
        if max_length is not None:
            violations |= lengths > max_length
        
        return violations

    # ------------------------------------------------------------------
    # ADR 0006 / issue #13 — BA-friendly "native" predicates.
    #
    # The BA-friendly rules template (see ``prompts/generate-rules-csv.md``)
    # and ``BARulesTemplateConverter`` emit a vocabulary of operators that
    # the historical ``_validate_field`` dispatcher did not understand
    # (``not_empty``, ``numeric``, ``date_format``, ``valid_values``,
    # ``min_value``, ``max_value``, ``exact_length``, ``min_length``). Every
    # rule using these operators failed with ``Unknown operator: <name>``,
    # making every BA-friendly JSON in the repo non-executable. These eight
    # methods reconcile the two halves. They are strictly additive — the
    # existing predicates above are unchanged. See ADR 0006 for the full
    # mask table and SHAW ATOCTRAN smoke findings for the reproduction
    # that surfaced this bug.
    # ------------------------------------------------------------------

    def validate_not_empty(self, df: pd.DataFrame, field: str) -> pd.Series:
        """Flag rows where *field* is null, empty, or whitespace-only.

        Semantically identical to :meth:`validate_not_null` — kept as a
        separate method so the BA-friendly operator name (``not_empty``)
        has a literal predicate to dispatch to and so future refinements
        can diverge without touching the legacy ``not_null`` path.

        Args:
            df: DataFrame to validate.
            field: Column name to check.

        Returns:
            Boolean mask where ``True`` indicates a violation.
        """
        series = df[field]
        is_null = series.isna()
        is_blank = series.astype(str).str.strip() == ""
        return is_null | is_blank

    def validate_numeric_format(self, df: pd.DataFrame, field: str) -> pd.Series:
        """Flag rows where *field* is non-empty but not numerically parseable.

        Empty / NaN cells are *not* flagged — pair with ``not_empty`` to
        require presence. ``pd.to_numeric`` is permissive: it accepts
        signed values, decimals, and leading zeros, matching BA workbook
        semantics per ADR 0006's deferred-questions section.

        Args:
            df: DataFrame to validate.
            field: Column name to check.

        Returns:
            Boolean mask where ``True`` indicates a non-numeric value.
        """
        series = df[field]
        coerced = pd.to_numeric(series, errors="coerce")
        # A value is a violator when:
        #   - it failed to coerce (NaN in ``coerced``)
        #   - AND the original cell was not itself empty/NaN/whitespace
        # so that empties don't double-count against numeric format.
        original_present = series.notna() & (series.astype(str).str.strip() != "")
        return coerced.isna() & original_present

    def validate_date_format(
        self, df: pd.DataFrame, field: str, fmt: str
    ) -> pd.Series:
        """Flag rows whose *field* value does not match the named date format.

        Format tokens are looked up in
        :data:`src.validators._date_formats.DATE_FORMAT_REGEX`. Unknown
        formats fall through to ``^\\d+$`` (mirroring the historical BA
        converter behavior).

        Args:
            df: DataFrame to validate.
            field: Column name to check.
            fmt: Format token (e.g. ``"CCYYMMDD"``). Case-insensitive.

        Returns:
            Boolean mask where ``True`` indicates the value does not match
            the format regex. Empty cells are flagged as violators.
        """
        pattern = regex_for_format(fmt)
        series = df[field].astype(str).str.strip()
        # Null / NaN cells coerce to "nan" via astype(str). Skip them —
        # they are caught by not_empty when the field is required. Flagging
        # "nan" as a date-format violation produces spurious errors on
        # optional date fields that are legitimately blank in some rows.
        is_null = df[field].isna()
        return ~series.str.match(pattern, na=False) & ~is_null

    def validate_valid_values(
        self, df: pd.DataFrame, field: str, values: List[Any]
    ) -> pd.Series:
        """Flag rows whose *field* value is not in *values*.

        Values are stripped on both sides before comparison so that
        fixed-width padded cells (``'LS  '``) match a value list of
        ``['LS']``. Equivalent to ``validate_list(..., 'in', values)`` —
        provided as a named predicate for BA-friendly operator dispatch.

        Args:
            df: DataFrame to validate.
            field: Column name to check.
            values: Allowed value list.

        Returns:
            Boolean mask where ``True`` indicates the value is not in the
            allowed set.
        """
        series = df[field].astype(str).str.strip()
        allowed = [str(v).strip() for v in values]
        # Null / NaN cells coerce to the string "nan" via astype(str). These
        # should not be flagged by valid_values — they are either optional
        # fields (in which case the absence is acceptable) or required fields
        # (in which case the not_empty rule already catches them). Flagging
        # "nan" as an invalid value produces spurious violations and confuses
        # BA review. Surfaced by SHAW TRANERT RCF-DES-COD-REC (optional field
        # with a default value but empty in some rows).
        is_null = df[field].isna()
        return ~series.isin(allowed) & ~is_null

    def validate_min_value(
        self, df: pd.DataFrame, field: str, value: Any
    ) -> pd.Series:
        """Flag rows where numeric *field* is strictly less than *value*.

        Non-numeric values are *not* flagged here — they violate
        ``numeric`` instead. Use both rules together when both numeric
        format and a floor are required.

        Args:
            df: DataFrame to validate.
            field: Column name to check.
            value: Inclusive minimum (rows with ``series == value`` pass).

        Returns:
            Boolean mask where ``True`` indicates a below-minimum value.
        """
        series = pd.to_numeric(df[field], errors="coerce")
        return series < value

    def validate_max_value(
        self, df: pd.DataFrame, field: str, value: Any
    ) -> pd.Series:
        """Flag rows where numeric *field* is strictly greater than *value*.

        Mirrors :meth:`validate_min_value` for the upper bound.

        Args:
            df: DataFrame to validate.
            field: Column name to check.
            value: Inclusive maximum (rows with ``series == value`` pass).

        Returns:
            Boolean mask where ``True`` indicates an above-maximum value.
        """
        series = pd.to_numeric(df[field], errors="coerce")
        return series > value

    def validate_exact_length(
        self, df: pd.DataFrame, field: str, length: int
    ) -> pd.Series:
        """Flag rows whose *field* string length is not exactly *length*.

        Whitespace is *not* stripped — fixed-width padded cells are
        intentionally measured at their full width.

        Args:
            df: DataFrame to validate.
            field: Column name to check.
            length: Required exact string length.

        Returns:
            Boolean mask where ``True`` indicates a length mismatch.
        """
        series = df[field].astype(str)
        return series.str.len() != length

    def validate_min_length(
        self, df: pd.DataFrame, field: str, length: int
    ) -> pd.Series:
        """Flag rows whose *field* string length is less than *length*.

        Args:
            df: DataFrame to validate.
            field: Column name to check.
            length: Minimum required string length.

        Returns:
            Boolean mask where ``True`` indicates a too-short value.
        """
        series = df[field].astype(str)
        return series.str.len() < length
