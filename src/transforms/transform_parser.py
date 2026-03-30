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

Anything else — including complex conditional expressions — returns a noop
``Transform`` so that downstream code can safely fall back to a direct
source-field copy.
"""

from __future__ import annotations

import re
from typing import Optional

from src.transforms.models import (
    BlankTransform,
    ConcatPart,
    ConcatTransform,
    ConstantTransform,
    DefaultTransform,
    FieldMapTransform,
    Transform,
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
