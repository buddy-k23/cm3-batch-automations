"""Parse free-text mapping transformation descriptions into typed Transform objects.

Recognises patterns found in real Shaw→C360 mapping spreadsheets:

- ``Default to 'VALUE'`` / ``Default to VALUE`` / ``Default = VALUE``
- ``Nullable --> Leave Blank`` / ``Nullable --> 'FILL'``
- ``Leave Blank`` / ``Leave blank <spaces>``
- ``Pass Blank <spaces>``
- ``Initialize to spaces``
- ``Pass 'VALUE'``
- ``Hard-code to 'VALUE'`` / ``Hard-Code to 'VALUE'`` / ``Hardcode to 'VALUE'``
- ``FIELD1 + FIELD2 + FIELD3`` (concatenation)
- ``LPAD(FIELD,N) + FIELD2`` (left-padded concatenation)
- ``FIELD_NAME`` (bare uppercase identifier — direct field map)
- ``IF <cond> THEN <branch> [ELSE <branch>]`` (conditional, Phase 3d)
- ``Convert to CCYYMMDD`` / ``Convert to YYYYMMDD`` (date format, Phase 4a)
- ``Convert to MM/DD/CCYY`` (date format, Phase 4a)
- ``Date format CCYYMMDD`` / ``Format as CCYYMMDD`` (date format, Phase 4a)

Conditional conditions supported:

- ``IF FIELD not null THEN ...`` → :class:`NullCheckCondition` (negate=True)
- ``IF FIELD IS NULL THEN ...`` → :class:`NullCheckCondition` (negate=False)
- ``IF FIELD IS NOT NULL THEN ...`` → :class:`NullCheckCondition` (negate=True)
- ``IF FIELD = 'VALUE' THEN ...`` → :class:`EqualityCondition`
- ``IF FIELD != 'VALUE' THEN ...`` / ``<>`` → :class:`EqualityCondition` (negate=True)
- ``IF FIELD = '7' or '8' THEN ...`` → :class:`InCondition`
- ``IF FIELD IN ('A','B') THEN ...`` → :class:`InCondition`

Anything else — including complex conditional expressions not matching these
patterns — returns a noop ``Transform`` so that downstream code can safely
fall back to a direct source-field copy.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from src.transforms.models import (
    BlankTransform,
    ConcatPart,
    ConcatTransform,
    ConditionalTransform,
    ConstantTransform,
    DateFormatTransform,
    DefaultTransform,
    EqualityCondition,
    FieldMapTransform,
    InCondition,
    NullCheckCondition,
    NumericFormatTransform,
    PadTransform,
    ScaleTransform,
    SequentialNumberTransform,
    Transform,
    TruncateTransform,
)

# ---------------------------------------------------------------------------
# Pre-compiled patterns (order matters — more specific first)
# ---------------------------------------------------------------------------

# "Default to VALUE" or "Default = VALUE" (unquoted, single-token value)
# Captures a contiguous non-whitespace token immediately after "to" / "=".
_DEFAULT_UNQUOTED_RE = re.compile(
    r"^default\s*(?:to|=)\s*(\S+)",
    re.IGNORECASE,
)

# "Nullable --> Leave Blank" or "Nullable --> '<FILL>'"
_NULLABLE_LEAVE_BLANK_RE = re.compile(
    r"nullable\s*-->\s*leave\s+blank",
    re.IGNORECASE,
)
_NULLABLE_FILL_RE = re.compile(
    r"nullable\s*-->\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# "Leave Blank" / "Leave blank <spaces>"
_LEAVE_BLANK_RE = re.compile(r"^leave\s+blank", re.IGNORECASE)

# "Pass Blank <spaces>"
_PASS_BLANK_RE = re.compile(r"^pass\s+blank", re.IGNORECASE)

# "Initialize to spaces"
_INIT_SPACES_RE = re.compile(r"^initialize\s+to\s+spaces", re.IGNORECASE)

# "Pass 'VALUE'" — quoted token after 'pass' only
_PASS_CONSTANT_RE = re.compile(
    r"^pass\s+['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# "Hard-code to 'VALUE'" / "Hard-Code to 'VALUE'" / "Hardcode to 'VALUE'"
# Accepts single or double quotes.
_HARDCODE_RE = re.compile(
    r"^hard-?code\s+to\s+['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# Simple "Default to 'VALUE'" with strict quoted capture (used as fallback
# to avoid the greedy unquoted branch swallowing trailing context).
_DEFAULT_QUOTED_RE = re.compile(
    r"^default\s*(?:to|=)\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Phase 2: concatenation and field-map patterns
# ---------------------------------------------------------------------------

# One LPAD token: LPAD(FIELD,N) or LPAD(FIELD,N,'C') or LPAD(FIELD,N,"C")
_LPAD_PART_RE = re.compile(
    r"^LPAD\(\s*([A-Z][A-Z0-9_\-]*)\s*,\s*(\d+)(?:\s*,\s*['\"]?(.)(?:['\"])?)?\s*\)$",
    re.IGNORECASE,
)

# One bare field name token: uppercase letters, digits, underscore, hyphen
_BARE_FIELD_RE = re.compile(r"^[A-Z][A-Z0-9_\-]*$", re.IGNORECASE)

# Full concat expression: two or more tokens separated by " + "
# Must NOT contain "=" (which would indicate a target-field assignment formula).
_CONCAT_EXPR_RE = re.compile(
    r"^((?:LPAD\([^)]+\)|[A-Z][A-Z0-9_\-]*))"
    r"(?:\s*\+\s*((?:LPAD\([^)]+\)|[A-Z][A-Z0-9_\-]*)))+$",
    re.IGNORECASE,
)

# A single bare uppercase identifier (field name direct map)
# Matches only strings with uppercase letters (and digits/underscore/hyphen),
# no whitespace, at least 2 chars to avoid false positives on short acronyms
# that look like constants.
_FIELD_MAP_RE = re.compile(r"^[A-Z][A-Z0-9_\-]+$")

# ---------------------------------------------------------------------------
# Phase 3e: sequential numbering pattern
# ---------------------------------------------------------------------------

# Matches: "Sequential", "sequential number", "sequence" (case-insensitive)
_SEQUENTIAL_RE = re.compile(
    r"^(?:sequential(?:\s+number)?|sequence)$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Phase 4a: date format conversion patterns
# ---------------------------------------------------------------------------

# Maps a date-format token to (input_format, output_format) tuples.
_DATE_FORMAT_MAP = {
    "CCYYMMDD": ("%Y-%m-%d", "%Y%m%d"),
    "YYYYMMDD": ("%Y-%m-%d", "%Y%m%d"),
    "MM/DD/CCYY": ("%Y-%m-%d", "%m/%d/%Y"),
    "MM/DD/YYYY": ("%Y-%m-%d", "%m/%d/%Y"),
    # Phase 4e: reverse direction — MMDDYYYY input to CCYYMMDD output
    "MMDDCCYY": ("%m%d%Y", "%Y%m%d"),
    "MMDDYYYY": ("%m%d%Y", "%Y%m%d"),
}

# "Convert to CCYYMMDD" / "Date format CCYYMMDD" / "Format as CCYYMMDD"
_DATE_FORMAT_RE = re.compile(
    r"^(?:convert\s+to|date\s+format|format\s+as)\s+(\S+)$",
    re.IGNORECASE,
)

# Phase 4e: "Reformat date to TOKEN" / "Convert date TOKEN"
_DATE_FORMAT_ALT_RE = re.compile(
    r"^(?:reformat\s+date\s+to|convert\s+date)\s+(\S+)$",
    re.IGNORECASE,
)

# Phase 4e: "TOKEN format" (e.g. "CCYYMMDD format")
_DATE_FORMAT_TOKEN_RE = re.compile(
    r"^(\S+)\s+format$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Phase 4b: numeric format patterns
# ---------------------------------------------------------------------------

# "+9(N)" — signed COBOL picture clause
_SIGNED_PICTURE_RE = re.compile(r"^\+9\((\d+)\)$", re.IGNORECASE)

# "9(N)" — unsigned COBOL picture clause
_UNSIGNED_PICTURE_RE = re.compile(r"^9\((\d+)\)$", re.IGNORECASE)

# "Signed numeric, length N"
_SIGNED_NUMERIC_RE = re.compile(
    r"^signed\s+numeric\s*,\s*length\s+(\d+)$",
    re.IGNORECASE,
)

# "Zero-pad to N"
_ZERO_PAD_RE = re.compile(r"^zero-?pad\s+to\s+(\d+)$", re.IGNORECASE)

# "Pad to N digits"
_PAD_TO_DIGITS_RE = re.compile(r"^pad\s+to\s+(\d+)\s+digits$", re.IGNORECASE)

# Phase 4e: "N-digit zero-filled" / "N-digit zero-fill"
_N_DIGIT_ZERO_FILL_RE = re.compile(r"^(\d+)-digit\s+zero-?fill(?:ed)?$", re.IGNORECASE)

# Phase 4e: "Zero-fill to N positions"
_ZERO_FILL_POSITIONS_RE = re.compile(r"^zero-?fill\s+to\s+(\d+)\s+positions?$", re.IGNORECASE)

# Phase 4e: "Signed N-digit"
_SIGNED_N_DIGIT_RE = re.compile(r"^signed\s+(\d+)-digit$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Phase 4c: scale (multiply/divide) patterns
# ---------------------------------------------------------------------------

# "Multiply by N" — N may be an integer or decimal
_MULTIPLY_RE = re.compile(
    r"^multiply\s+by\s+(\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)

# "Divide by N" — N may be an integer or decimal
_DIVIDE_RE = re.compile(
    r"^divide\s+by\s+(\d+(?:\.\d+)?)$",
    re.IGNORECASE,
)

# Phase 4e: "Scale by N" / "Times by N"
_SCALE_BY_RE = re.compile(r"^(?:scale|times)\s+by\s+(\d+(?:\.\d+)?)$", re.IGNORECASE)

# Phase 4e: "Times N"
_TIMES_N_RE = re.compile(r"^times\s+(\d+(?:\.\d+)?)$", re.IGNORECASE)

# Phase 4e: "Divide result by N"
_DIVIDE_RESULT_RE = re.compile(r"^divide\s+result\s+by\s+(\d+(?:\.\d+)?)$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Phase 4d: pad and truncate patterns
# ---------------------------------------------------------------------------

# "Left pad to N with 'C'" / "LPAD to N with 'C'" — left-pad with pad_char
_LEFT_PAD_RE = re.compile(
    r"^(?:left\s+pad|lpad)\s+to\s+(\d+)(?:\s+with\s+['\"]?(.))?",
    re.IGNORECASE,
)

# "Right pad to N" / "Right pad to N with 'C'" — right-pad
_RIGHT_PAD_RE = re.compile(
    r"^right\s+pad\s+to\s+(\d+)(?:\s+with\s+['\"]?(.)['\"]?)?",
    re.IGNORECASE,
)

# "Pad to N with spaces" / "Pad to N" — generic right-pad
_PAD_SPACES_RE = re.compile(
    r"^pad\s+to\s+(\d+)(?:\s+with\s+spaces)?",
    re.IGNORECASE,
)

# "Truncate to N" / "Truncate to N chars" / "Truncate to N characters"
_TRUNCATE_N_RE = re.compile(
    r"^truncate\s+to\s+(\d+)(?:\s+chars?(?:acters?)?)?$",
    re.IGNORECASE,
)

# "Truncate decimal places" — special annotation, treated as noop-length TruncateTransform
_TRUNCATE_DECIMAL_RE = re.compile(
    r"^truncate\s+decimal\s+places?$",
    re.IGNORECASE,
)

# Phase 4e: "Space-pad to N"
_SPACE_PAD_RE = re.compile(r"^space-?pad\s+to\s+(\d+)$", re.IGNORECASE)

# Phase 4e: "Zero-fill left to N"
_ZERO_FILL_LEFT_RE = re.compile(r"^zero-?fill\s+left\s+to\s+(\d+)$", re.IGNORECASE)

# Phase 4e: "Pad left N zeros"
_PAD_LEFT_ZEROS_RE = re.compile(r"^pad\s+left\s+(\d+)\s+zeros?$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Phase 3d: conditional IF/THEN/ELSE patterns
# ---------------------------------------------------------------------------

# Top-level IF...THEN...ELSE structure.  The ELSE clause is optional.
# Uses non-greedy matching on the condition so THEN is found correctly.
# Does NOT match if a semicolon separates THEN/ELSE (unsupported syntax).
_IF_THEN_ELSE_RE = re.compile(
    r"^IF\s+(.+?)\s+THEN\s+(.+?)(?:\s+ELSE\s+(.+))?$",
    re.IGNORECASE,
)

# Condition: FIELD not null  (case-insensitive)
_COND_NOT_NULL_RE = re.compile(
    r"^([A-Z][A-Z0-9_\-]*)\s+not\s+null$",
    re.IGNORECASE,
)

# Condition: FIELD IS NULL / FIELD IS NOT NULL
_COND_IS_NULL_RE = re.compile(
    r"^([A-Z][A-Z0-9_\-]*)\s+IS\s+(NOT\s+)?NULL$",
    re.IGNORECASE,
)

# Condition: FIELD IN ('A', 'B', ...) — explicit IN list
_COND_IN_LIST_RE = re.compile(
    r"^([A-Z][A-Z0-9_\-]*)\s+IN\s*\((.+)\)$",
    re.IGNORECASE,
)

# Condition: FIELD = 'VALUE' or 'VALUE2' ...  (or-sugar → InCondition)
_COND_EQ_OR_RE = re.compile(
    r"^([A-Z][A-Z0-9_\-]*)\s*=\s*['\"]([^'\"]+)['\"]\s+or\s+['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)

# Condition: FIELD = 'VALUE' / FIELD != 'VALUE' / FIELD <> 'VALUE'
_COND_EQ_RE = re.compile(
    r"^([A-Z][A-Z0-9_\-]*)\s*(!=|<>|=)\s*['\"]([^'\"]+)['\"]$",
    re.IGNORECASE,
)

# A quoted literal in a branch position: 'VALUE' or "VALUE"
_QUOTED_VALUE_RE = re.compile(r"^['\"]([^'\"]+)['\"]$")

# A bare uppercase field name used as a branch (direct field reference)
_BRANCH_FIELD_RE = re.compile(r"^[A-Z][A-Z0-9_\-]+$")


def _parse_condition(
    condition_text: str,
) -> "Optional[NullCheckCondition | EqualityCondition | InCondition]":
    """Parse a condition fragment from an IF expression.

    Args:
        condition_text: The text between ``IF`` and ``THEN``, stripped.

    Returns:
        A condition object (:class:`NullCheckCondition`, :class:`EqualityCondition`,
        or :class:`InCondition`), or ``None`` if the text is unrecognised.
    """
    t = condition_text.strip()

    # "FIELD not null"
    m = _COND_NOT_NULL_RE.match(t)
    if m:
        return NullCheckCondition(field=m.group(1).upper(), negate=True)

    # "FIELD IS NULL" / "FIELD IS NOT NULL"
    m = _COND_IS_NULL_RE.match(t)
    if m:
        negate = m.group(2) is not None  # group(2) is "NOT " when present
        return NullCheckCondition(field=m.group(1).upper(), negate=negate)

    # "FIELD IN ('A', 'B', 'C')"
    m = _COND_IN_LIST_RE.match(t)
    if m:
        field_name = m.group(1).upper()
        raw_list = m.group(2)
        values = [v.strip().strip("'\"") for v in raw_list.split(",") if v.strip()]
        return InCondition(field=field_name, values=values)

    # "FIELD = 'V1' or 'V2'" (or-sugar syntax → InCondition)
    m = _COND_EQ_OR_RE.match(t)
    if m:
        field_name = m.group(1).upper()
        values = [m.group(2), m.group(3)]
        return InCondition(field=field_name, values=values)

    # "FIELD = 'VALUE'" / "FIELD != 'VALUE'" / "FIELD <> 'VALUE'"
    m = _COND_EQ_RE.match(t)
    if m:
        field_name = m.group(1).upper()
        operator = m.group(2)
        value = m.group(3)
        negate = operator in ("!=", "<>")
        return EqualityCondition(field=field_name, value=value, negate=negate)

    return None


def _parse_branch(branch_text: str) -> Transform:
    """Parse a THEN or ELSE branch fragment into a Transform.

    A branch can be:

    - A quoted literal ``'VALUE'`` → :class:`ConstantTransform`
    - A bare UPPERCASE field name → :class:`FieldMapTransform`
    - Any other text is delegated to :func:`parse_transform` recursively
      (handles ``Default to``, ``Leave Blank``, etc.)

    Args:
        branch_text: The raw branch fragment, stripped.

    Returns:
        A ``Transform`` subclass matching the branch content.
    """
    t = branch_text.strip()

    # Quoted literal → ConstantTransform
    m = _QUOTED_VALUE_RE.match(t)
    if m:
        return ConstantTransform(value=m.group(1))

    # Bare uppercase field name → FieldMapTransform
    if _BRANCH_FIELD_RE.match(t):
        return FieldMapTransform(source_field=t.upper())

    # Delegate to parse_transform for known patterns (Default to, Leave Blank, etc.)
    return parse_transform(t)


def _parse_conditional(text: str) -> "Optional[ConditionalTransform]":
    """Attempt to parse an IF/THEN/ELSE expression from *text*.

    Recognises::

        IF <condition> THEN <branch> [ELSE <branch>]

    Keywords (IF, THEN, ELSE) are case-insensitive.  The ELSE clause is
    optional; when absent the else branch defaults to a noop transform.
    Semicolons in the text prevent matching (unsupported syntax).

    Args:
        text: Full transformation text, stripped.

    Returns:
        A :class:`ConditionalTransform` when successfully parsed, or ``None``
        when the text does not match the expected IF/THEN/ELSE structure.
    """
    # Reject semicolon-separated syntax which we do not support.
    if ";" in text:
        return None

    m = _IF_THEN_ELSE_RE.match(text)
    if not m:
        return None

    condition_text = m.group(1).strip()
    then_text = m.group(2).strip()
    else_text = m.group(3).strip() if m.group(3) else None

    condition = _parse_condition(condition_text)
    if condition is None:
        return None

    then_transform = _parse_branch(then_text)
    else_transform = _parse_branch(else_text) if else_text is not None else Transform(type="noop")

    return ConditionalTransform(
        condition=condition,
        then_transform=then_transform,
        else_transform=else_transform,
    )


def _parse_concat_part(token: str) -> ConcatPart:
    """Parse a single token into a :class:`ConcatPart`.

    Args:
        token: A stripped token such as ``"LPAD(BR,3,'0')"`` or ``"CUS"``.

    Returns:
        A :class:`ConcatPart` with optional lpad settings populated.
    """
    m = _LPAD_PART_RE.match(token.strip())
    if m:
        field_name = m.group(1).upper()
        lpad_width = int(m.group(2))
        lpad_char = m.group(3) if m.group(3) else " "
        return ConcatPart(field_name=field_name, lpad_width=lpad_width, lpad_char=lpad_char)
    return ConcatPart(field_name=token.strip().upper())


def parse_transform(text: Optional[str]) -> Transform:
    """Parse a free-text transformation description into a typed Transform.

    Args:
        text: The raw transformation text from a mapping spreadsheet cell.
            May be ``None`` or empty.

    Returns:
        A ``Transform`` subclass instance whose type matches the recognised
        pattern, or a base ``Transform(type='noop')`` when the text is empty,
        ``None``, or unrecognised.

    Examples:
        >>> parse_transform("Default to '100030'")
        DefaultTransform(value='100030', type='default')

        >>> parse_transform("Nullable --> Leave Blank")
        BlankTransform(fill_char=' ', fill_value='', type='blank')

        >>> parse_transform("Pass '000'")
        ConstantTransform(value='000', type='constant')

        >>> parse_transform(None)
        Transform(type='noop')
    """
    if not text or not text.strip():
        return Transform(type="noop")

    t = text.strip()

    # --- Phase 3d: conditional IF/THEN/ELSE (check first, before all others) ---

    conditional = _parse_conditional(t)
    if conditional is not None:
        return conditional

    # --- Phase 4b: numeric format patterns ---

    m = _SIGNED_PICTURE_RE.match(t)
    if m:
        n = int(m.group(1))
        return NumericFormatTransform(length=n + 1, signed=True)

    m = _UNSIGNED_PICTURE_RE.match(t)
    if m:
        n = int(m.group(1))
        return NumericFormatTransform(length=n, signed=False)

    m = _SIGNED_NUMERIC_RE.match(t)
    if m:
        return NumericFormatTransform(length=int(m.group(1)), signed=True)

    m = _ZERO_PAD_RE.match(t)
    if m:
        return NumericFormatTransform(length=int(m.group(1)), signed=False)

    m = _PAD_TO_DIGITS_RE.match(t)
    if m:
        return NumericFormatTransform(length=int(m.group(1)), signed=False)

    # --- Phase 4e: extended numeric format patterns ---

    m = _N_DIGIT_ZERO_FILL_RE.match(t)
    if m:
        return NumericFormatTransform(length=int(m.group(1)), signed=False)

    m = _ZERO_FILL_POSITIONS_RE.match(t)
    if m:
        return NumericFormatTransform(length=int(m.group(1)), signed=False)

    m = _SIGNED_N_DIGIT_RE.match(t)
    if m:
        return NumericFormatTransform(length=int(m.group(1)), signed=True)

    # --- Phase 3e: sequential numbering ---

    if _SEQUENTIAL_RE.match(t):
        return SequentialNumberTransform()

    # --- Phase 4a: date format conversion ---

    m = _DATE_FORMAT_RE.match(t)
    if m:
        token = m.group(1).upper()
        if token in _DATE_FORMAT_MAP:
            input_fmt, output_fmt = _DATE_FORMAT_MAP[token]
            return DateFormatTransform(input_format=input_fmt, output_format=output_fmt)

    # --- Phase 4e: extended date format patterns ---

    m = _DATE_FORMAT_ALT_RE.match(t)
    if m:
        token = m.group(1).upper()
        if token in _DATE_FORMAT_MAP:
            input_fmt, output_fmt = _DATE_FORMAT_MAP[token]
            return DateFormatTransform(input_format=input_fmt, output_format=output_fmt)

    m = _DATE_FORMAT_TOKEN_RE.match(t)
    if m:
        token = m.group(1).upper()
        if token in _DATE_FORMAT_MAP:
            input_fmt, output_fmt = _DATE_FORMAT_MAP[token]
            return DateFormatTransform(input_format=input_fmt, output_format=output_fmt)

    # --- Phase 4c: scale (multiply / divide) ---

    m = _MULTIPLY_RE.match(t)
    if m:
        factor = float(m.group(1))
        return ScaleTransform(factor=factor, decimal_places=0)

    m = _DIVIDE_RE.match(t)
    if m:
        divisor = float(m.group(1))
        factor = 1.0 / divisor
        # Compute decimal_places from the magnitude of the divisor so that
        # "Divide by 100" → decimal_places=2, "Divide by 1000" → 3, etc.
        # For non-power-of-ten divisors, fall back to -1 (auto).
        log_val = math.log10(divisor)
        if log_val == int(log_val) and log_val > 0:
            decimal_places = int(log_val)
        else:
            decimal_places = -1
        return ScaleTransform(factor=factor, decimal_places=decimal_places)

    # --- Phase 4e: extended scale patterns ---

    m = _SCALE_BY_RE.match(t)
    if m:
        return ScaleTransform(factor=float(m.group(1)), decimal_places=0)

    m = _TIMES_N_RE.match(t)
    if m:
        return ScaleTransform(factor=float(m.group(1)), decimal_places=0)

    m = _DIVIDE_RESULT_RE.match(t)
    if m:
        divisor = float(m.group(1))
        return ScaleTransform(factor=1.0 / divisor, decimal_places=0)

    # --- Blank / space patterns (check before generic "pass" pattern) ---

    if _INIT_SPACES_RE.match(t):
        return BlankTransform(fill_char=" ")

    if _LEAVE_BLANK_RE.match(t):
        return BlankTransform()

    if _PASS_BLANK_RE.match(t):
        return BlankTransform()

    # --- Nullable patterns ---

    if _NULLABLE_LEAVE_BLANK_RE.search(t):
        return BlankTransform()

    m = _NULLABLE_FILL_RE.search(t)
    if m:
        return BlankTransform(fill_value=m.group(1))

    # --- Constant patterns ---

    m = _HARDCODE_RE.match(t)
    if m:
        return ConstantTransform(value=m.group(1))

    m = _PASS_CONSTANT_RE.match(t)
    if m:
        return ConstantTransform(value=m.group(1))

    # --- Default patterns (quoted first, then unquoted) ---

    m = _DEFAULT_QUOTED_RE.match(t)
    if m:
        return DefaultTransform(value=m.group(1))

    m = _DEFAULT_UNQUOTED_RE.match(t)
    if m:
        return DefaultTransform(value=m.group(1).strip())

    # --- Phase 4d: pad patterns ---

    m = _LEFT_PAD_RE.match(t)
    if m:
        length = int(m.group(1))
        pad_char = m.group(2) if m.group(2) else " "
        return PadTransform(length=length, pad_char=pad_char, direction="left")

    m = _RIGHT_PAD_RE.match(t)
    if m:
        length = int(m.group(1))
        pad_char = m.group(2) if m.group(2) else " "
        return PadTransform(length=length, pad_char=pad_char, direction="right")

    m = _PAD_SPACES_RE.match(t)
    if m:
        length = int(m.group(1))
        return PadTransform(length=length, pad_char=" ", direction="right")

    # --- Phase 4d: truncate patterns ---

    if _TRUNCATE_DECIMAL_RE.match(t):
        return TruncateTransform(length=0)

    m = _TRUNCATE_N_RE.match(t)
    if m:
        return TruncateTransform(length=int(m.group(1)))

    # --- Phase 4e: extended pad patterns ---

    m = _SPACE_PAD_RE.match(t)
    if m:
        return PadTransform(length=int(m.group(1)), pad_char=" ", direction="right")

    m = _ZERO_FILL_LEFT_RE.match(t)
    if m:
        return PadTransform(length=int(m.group(1)), pad_char="0", direction="left")

    m = _PAD_LEFT_ZEROS_RE.match(t)
    if m:
        return PadTransform(length=int(m.group(1)), pad_char="0", direction="left")

    # --- Phase 2: concatenation (two or more fields joined by +) ---

    # Reject assignment formulas like "TARGET = FIELD1 + FIELD2"
    if "=" not in t and _CONCAT_EXPR_RE.match(t):
        tokens = [tok.strip() for tok in t.split("+")]
        parts = [_parse_concat_part(tok) for tok in tokens if tok.strip()]
        if len(parts) >= 2:
            return ConcatTransform(parts=parts)

    # --- Phase 2: direct field map (bare uppercase identifier) ---

    if _FIELD_MAP_RE.match(t):
        return FieldMapTransform(source_field=t.upper())

    return Transform(type="noop")
