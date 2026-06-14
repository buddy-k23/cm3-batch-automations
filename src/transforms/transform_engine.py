"""Apply a Transform to a source field value, producing a final output string.

This module is the execution side of the transform system.  It consumes the
typed ``Transform`` objects produced by ``transform_parser`` and produces
strings ready for output-file writing.

Padding / truncation rules
--------------------------
When ``field_length`` is a positive integer the result is always exactly
``field_length`` characters:

- Values shorter than ``field_length`` are **right-padded with spaces**.
- Values longer than ``field_length`` are **truncated on the right**.

When ``field_length`` is zero or negative, no padding or truncation is applied.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from src.transforms.condition_evaluator import evaluate_condition
from src.transforms.models import (
    BlankTransform,
    ConcatTransform,
    ConditionalTransform,
    ConstantTransform,
    DateFormatTransform,
    DefaultTransform,
    FieldMapTransform,
    NumericFormatTransform,
    PadTransform,
    ScaleTransform,
    SequentialNumberTransform,
    Transform,
    TruncateTransform,
)

# Sentinel used to detect the absence of a meaningful source value.
_ABSENT = frozenset({"", None})


def _is_absent(value: Optional[str]) -> bool:
    """Return True when *value* is None, empty, or whitespace-only."""
    if value is None:
        return True
    return not value.strip()


def _fit(value: str, field_length: int, pad_char: str = " ") -> str:
    """Pad or truncate *value* to exactly *field_length* characters.

    Args:
        value: The string to fit.
        field_length: Desired output width.  Values ≤ 0 disable fitting.
        pad_char: Character used for right-padding.  Defaults to ``' '``.

    Returns:
        The fitted string, or the original string when ``field_length <= 0``.
    """
    if field_length <= 0:
        return value
    if len(value) >= field_length:
        return value[:field_length]
    return value + pad_char * (field_length - len(value))


def _lpad(value: str, width: int, pad_char: str = " ") -> str:
    """Left-pad *value* to *width* characters using *pad_char*.

    LPAD never truncates: values already at or exceeding *width* are returned
    unchanged.

    Args:
        value: String to pad.
        width: Desired minimum width.  ``0`` or negative disables padding.
        pad_char: Character used for left-padding.  Defaults to ``' '``.

    Returns:
        Left-padded string.
    """
    if width <= 0 or len(value) >= width:
        return value
    return pad_char * (width - len(value)) + value


def _apply_numeric_format(raw_source: str, transform: "NumericFormatTransform") -> str:
    """Apply a :class:`NumericFormatTransform` to *raw_source*.

    Args:
        raw_source: The raw source string (may be empty or whitespace).
        transform: The ``NumericFormatTransform`` configuration.

    Returns:
        Zero-padded numeric string with optional sign prefix, or
        ``transform.default_value`` when the source is absent or unparseable.
    """
    if not raw_source or not raw_source.strip():
        return transform.default_value

    try:
        value = float(raw_source.strip())
    except (ValueError, TypeError):
        return transform.default_value

    if transform.decimal_places > 0:
        value = value * (10 ** transform.decimal_places)

    int_value = round(value)
    negative = int_value < 0
    abs_value = abs(int_value)

    # Width available for digits (subtract 1 for sign char when signed)
    digit_width = (transform.length - 1) if transform.signed else transform.length
    digit_width = max(digit_width, 0)

    digit_str = str(abs_value).zfill(digit_width) if digit_width > 0 else str(abs_value)

    if transform.signed:
        sign = "-" if negative else "+"
        return sign + digit_str

    return digit_str


def apply_transform(
    source_value: Optional[str],
    transform: Transform,
    field_length: int = 0,
    row: Optional[dict] = None,
    counter: "Optional[object]" = None,
) -> str:
    """Apply *transform* to *source_value* and return the output string.

    Args:
        source_value: The raw value read from the source file field.  ``None``
            is treated identically to an empty string.
        transform: A ``Transform`` instance (or subclass) produced by
            ``parse_transform``.
        field_length: Target output field width.  ``0`` means no constraint.
            Positive values trigger right-padding / right-truncation.
        row: Optional mapping of field names to values for transforms that
            reference other fields (e.g. :class:`ConcatTransform`,
            :class:`FieldMapTransform`).
        counter: Optional
            :class:`~src.transforms.sequential_counter.SequentialCounter`
            instance.  Required for stateful
            :class:`~src.transforms.models.SequentialNumberTransform`
            processing.  When ``None``, sequential transforms return
            ``str(transform.start)`` as a stateless fallback.

    Returns:
        The transformed string, fitted to ``field_length`` when positive.

    Examples:
        >>> apply_transform("REAL", DefaultTransform(value="FB"))
        'REAL'

        >>> apply_transform("", DefaultTransform(value="FB"))
        'FB'

        >>> apply_transform("X", ConstantTransform(value="000"))
        '000'

        >>> apply_transform("AB", Transform(type="noop"), field_length=5)
        'AB   '
    """
    raw_source = "" if source_value is None else source_value
    safe_row: dict = row if row is not None else {}

    if isinstance(transform, ConstantTransform):
        result = transform.value

    elif isinstance(transform, DefaultTransform):
        result = raw_source if not _is_absent(raw_source) else transform.value

    elif isinstance(transform, BlankTransform):
        if transform.fill_value:
            base = transform.fill_value
            # Extend fill_value with fill_char to meet field_length, then fit.
            if field_length > 0 and len(base) < field_length:
                base = base + transform.fill_char * (field_length - len(base))
            result = base
        else:
            # No explicit fill_value: produce fill_char * field_length, or ""
            result = transform.fill_char * field_length if field_length > 0 else ""

    elif isinstance(transform, ConcatTransform):
        pieces = []
        for part in transform.parts:
            value = safe_row.get(part.field_name, "")
            if part.lpad_width > 0:
                value = _lpad(value, part.lpad_width, part.lpad_char)
            pieces.append(value)
        result = "".join(pieces)

    elif isinstance(transform, FieldMapTransform):
        result = safe_row.get(transform.source_field, "")

    elif isinstance(transform, SequentialNumberTransform):
        if counter is not None:
            result = counter.next_value(transform)
        else:
            # Stateless fallback: always emit str(start).
            raw = str(transform.start)
            if transform.pad_length is not None:
                raw = raw.zfill(transform.pad_length)
            result = raw

    elif isinstance(transform, NumericFormatTransform):
        result = _apply_numeric_format(raw_source, transform)

    elif isinstance(transform, DateFormatTransform):
        if _is_absent(raw_source):
            result = transform.default_value
        else:
            try:
                parsed_dt = datetime.strptime(raw_source.strip(), transform.input_format)
                result = parsed_dt.strftime(transform.output_format)
            except ValueError:
                result = transform.default_value

    elif isinstance(transform, ScaleTransform):
        if _is_absent(raw_source):
            result = transform.default_value
        else:
            try:
                numeric = float(raw_source)
            except (ValueError, TypeError):
                result = transform.default_value
            else:
                scaled = numeric * transform.factor
                if transform.decimal_places >= 0:
                    result = f"{scaled:.{transform.decimal_places}f}"
                else:
                    result = str(scaled)

    elif isinstance(transform, PadTransform):
        if transform.length <= 0 or len(raw_source) >= transform.length:
            result = raw_source
        elif transform.direction == "left":
            result = _lpad(raw_source, transform.length, transform.pad_char)
        else:
            result = raw_source + transform.pad_char * (transform.length - len(raw_source))

    elif isinstance(transform, TruncateTransform):
        n = transform.length
        if n <= 0 or len(raw_source) <= n:
            result = raw_source
        elif transform.from_end:
            result = raw_source[-n:]
        else:
            result = raw_source[:n]

    elif isinstance(transform, ConditionalTransform):
        branch = (
            transform.then_transform
            if evaluate_condition(transform.condition, safe_row)
            else transform.else_transform
        )
        # Recurse without applying _fit here; the recursive call handles fitting.
        return apply_transform(
            source_value, branch, field_length=field_length, row=row, counter=counter
        )

    else:
        # Noop — pass through.
        result = raw_source

    return _fit(result, field_length)
