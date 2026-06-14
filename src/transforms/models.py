"""Dataclasses representing parsed field-level transforms.

Each transform type is a lightweight value object.  The base ``Transform``
carries ``type='noop'`` and acts as a pass-through sentinel.  Subclasses add
the parameters specific to their behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Transform:
    """Base transform — pass-through sentinel.

    Attributes:
        type: Machine-readable transform kind. Defaults to ``'noop'``.
    """

    type: str = "noop"


@dataclass
class DefaultTransform(Transform):
    """Return the source value when present; otherwise fall back to *value*.

    Attributes:
        value: The fallback/default string to use when source is absent.
        type: Always ``'default'``.
    """

    value: str = ""
    type: str = field(default="default", init=False)

    def __post_init__(self) -> None:
        self.type = "default"


@dataclass
class BlankTransform(Transform):
    """Always output a blank (space-padded or fixed fill) value.

    Attributes:
        fill_char: Character used to pad when no explicit ``fill_value`` is
            set and a ``field_length`` is provided.  Defaults to ``' '``.
        fill_value: Optional explicit fill string.  When set, this takes
            priority over ``fill_char`` padding.  Defaults to ``''``.
        type: Always ``'blank'``.
    """

    fill_char: str = " "
    fill_value: str = ""
    type: str = field(default="blank", init=False)

    def __post_init__(self) -> None:
        self.type = "blank"


@dataclass
class ConstantTransform(Transform):
    """Always output a fixed constant, ignoring the source value entirely.

    Attributes:
        value: The constant string to emit unconditionally.
        type: Always ``'constant'``.
    """

    value: str = ""
    type: str = field(default="constant", init=False)

    def __post_init__(self) -> None:
        self.type = "constant"


@dataclass
class ConcatPart:
    """One field reference within a :class:`ConcatTransform`.

    Attributes:
        field_name: Name of the source row field to read.
        lpad_width: Left-pad the field value to this width before concatenating.
            ``0`` means no padding.
        lpad_char: Character used for left-padding.  Defaults to ``' '``.
    """

    field_name: str
    lpad_width: int = 0
    lpad_char: str = " "


@dataclass
class ConcatTransform(Transform):
    """Concatenate multiple source fields, with optional per-field LPAD.

    Attributes:
        parts: Ordered list of :class:`ConcatPart` objects describing each
            field to include in the concatenation.
        type: Always ``'concat'``.
    """

    parts: list = field(default_factory=list)  # list[ConcatPart]
    type: str = field(default="concat", init=False)

    def __post_init__(self) -> None:
        self.type = "concat"


@dataclass
class FieldMapTransform(Transform):
    """Map a named source field directly to the target field.

    Attributes:
        source_field: Name of the source row field whose value should be used.
        type: Always ``'field_map'``.
    """

    source_field: str = ""
    type: str = field(default="field_map", init=False)

    def __post_init__(self) -> None:
        self.type = "field_map"


@dataclass
class NullCheckCondition:
    """Condition that tests whether a named field is null (absent or blank).

    A field is considered null when it is absent from the row dict, ``None``,
    or a whitespace-only string.  Set ``negate=True`` to invert the test to
    *IS NOT NULL*.

    Attributes:
        field: The row field name to inspect.
        negate: When ``False`` (default) the condition is *IS NULL*; when
            ``True`` the condition is *IS NOT NULL*.
        type: Always ``'null_check'``.
    """

    field: str
    negate: bool = False
    type: str = field(default="null_check", init=False)

    def __post_init__(self) -> None:
        self.type = "null_check"


@dataclass
class EqualityCondition:
    """Condition that tests whether a named field equals a given value.

    Both the field value and the comparison value are stripped of leading and
    trailing whitespace before comparison.  Matching is case-sensitive.  A
    field that is absent from the row dict is treated as an empty string.

    Set ``negate=True`` to invert the test to *field != value*.

    Attributes:
        field: The row field name to inspect.
        value: The string to compare against.
        negate: When ``False`` (default) the condition is *field == value*;
            when ``True`` the condition is *field != value*.
        type: Always ``'equality'``.

    Example::

        cond = EqualityCondition(field="status", value="ACTIVE")
        evaluate_condition(cond, {"status": "ACTIVE"})   # True
        evaluate_condition(cond, {"status": "INACTIVE"}) # False

        neg = EqualityCondition(field="status", value="ACTIVE", negate=True)
        evaluate_condition(neg, {"status": "INACTIVE"})  # True
    """

    field: str
    value: str = ""
    negate: bool = False
    type: str = field(default="equality", init=False)

    def __post_init__(self) -> None:
        self.type = "equality"


@dataclass
class InCondition:
    """Condition that tests whether a named field's value is in a list.

    The field value and every entry in *values* are stripped of leading and
    trailing whitespace before the membership test.  A field absent from the
    row dict is treated as an empty string.

    Set ``negate=True`` to invert the test to *field NOT IN values*.

    Attributes:
        field: The row field name to inspect.
        values: The list of candidate strings to check membership against.
        negate: When ``False`` (default) the condition is *field IN values*;
            when ``True`` the condition is *field NOT IN values*.
        type: Always ``'in_condition'``.

    Example::

        cond = InCondition(field="type", values=["A", "B", "C"])
        evaluate_condition(cond, {"type": "B"})  # True
        evaluate_condition(cond, {"type": "D"})  # False

        neg = InCondition(field="type", values=["A", "B"], negate=True)
        evaluate_condition(neg, {"type": "D"})   # True
    """

    field: str
    values: list = field(default_factory=list)  # list[str]
    negate: bool = False
    type: str = field(default="in_condition", init=False)

    def __post_init__(self) -> None:
        self.type = "in_condition"


@dataclass
class SequentialNumberTransform(Transform):
    """Assign an incrementing sequence number to each record processed.

    The counter is stateful and managed externally by a
    :class:`~src.transforms.sequential_counter.SequentialCounter`.  When no
    counter is supplied to :func:`~src.transforms.transform_engine.apply_transform`
    the transform falls back to returning ``str(start)`` on every call.

    Attributes:
        start: The value emitted for the first record.  Defaults to ``1``.
        step: Amount to add to the counter after each emission.  Defaults to ``1``.
        pad_length: When set, the numeric string is zero-padded (with ``'0'``)
            to this total width.  Values already at or exceeding ``pad_length``
            are never truncated.  Defaults to ``None`` (no padding).
        type: Always ``'sequential'``.

    Example::

        t = SequentialNumberTransform(start=1, step=1, pad_length=5)
        # First record  → "00001"
        # Second record → "00002"
    """

    start: int = 1
    step: int = 1
    pad_length: Optional[int] = None
    type: str = field(default="sequential", init=False)

    def __post_init__(self) -> None:
        self.type = "sequential"


@dataclass
class NumericFormatTransform(Transform):
    """Zero-pad a numeric value to a fixed width, with optional sign prefix.

    The source value is parsed as a float, optionally scaled by
    ``10 ** decimal_places`` (moving the implied decimal point), rounded to
    an integer, and then zero-padded to ``length`` characters.  When
    ``signed=True`` one character of ``length`` is consumed by a leading
    ``'+'`` or ``'-'``.

    Attributes:
        length: Total output width including the sign character (when
            ``signed=True``).  A value of ``0`` disables padding entirely.
        signed: When ``True``, prepend ``'+'`` for non-negative values and
            ``'-'`` for negative values.  Defaults to ``False``.
        decimal_places: Number of implied decimal places.  The source value
            is multiplied by ``10 ** decimal_places`` before zero-padding,
            effectively shifting the decimal point.  Defaults to ``0``.
        default_value: Returned when the source value is absent (``None``,
            empty, or whitespace-only) or cannot be parsed as a number.
            Defaults to ``''``.
        type: Always ``'numeric_format'``.

    Example::

        t = NumericFormatTransform(length=13, signed=True)
        apply_transform("12345", t)       # -> "+000000012345"
        apply_transform("-12345", t)      # -> "-000000012345"

        t2 = NumericFormatTransform(length=13, signed=True, decimal_places=2)
        apply_transform("123.45", t2)     # -> "+000000012345"
    """

    length: int = 0
    signed: bool = False
    decimal_places: int = 0
    default_value: str = ""
    type: str = field(default="numeric_format", init=False)

    def __post_init__(self) -> None:
        self.type = "numeric_format"


@dataclass
class DateFormatTransform(Transform):
    """Convert a date string from one strptime format to another strftime format.

    When the source value is absent (``None``, empty, or whitespace-only) or
    cannot be parsed with *input_format*, *default_value* is returned instead.

    Attributes:
        input_format: :func:`datetime.strptime` format string for parsing the
            source value.  E.g. ``"%Y-%m-%d"``.
        output_format: :func:`datetime.strftime` format string for rendering
            the converted date.  E.g. ``"%Y%m%d"``.
        default_value: Value to return when the source is absent or
            unparseable.  Defaults to ``""`` (empty string).
        type: Always ``'date_format'``.

    Example::

        t = DateFormatTransform(input_format="%Y-%m-%d", output_format="%Y%m%d")
        apply_transform("2025-06-15", t)  # -> "20250615"
        apply_transform(None, t)          # -> ""
    """

    input_format: str = ""
    output_format: str = ""
    default_value: str = ""
    type: str = field(default="date_format", init=False)

    def __post_init__(self) -> None:
        self.type = "date_format"


@dataclass
class ScaleTransform(Transform):
    """Multiply a numeric source value by a fixed factor and return the result.

    The source value is parsed as a ``float``.  If the source is absent,
    blank, or cannot be parsed as a number, ``default_value`` is returned
    instead.

    Attributes:
        factor: Multiplier applied to the parsed source value.  Use values
            less than 1 to achieve division (e.g. ``0.01`` divides by 100).
            Defaults to ``1.0`` (identity).
        decimal_places: Number of digits after the decimal point in the
            output string.  When ``-1`` (the default), ``str()`` is used
            directly — trailing zeros are not explicitly controlled.  When
            ``>= 0``, the result is formatted with exactly that many decimal
            places via Python's ``f"{value:.{n}f}"`` format.
        default_value: Returned when the source is absent, blank, or
            cannot be parsed as a float.  Defaults to ``""`` (empty string).
        type: Always ``'scale'``.

    Example::

        ScaleTransform(factor=100, decimal_places=0)
        # "123.45" → "12345"

        ScaleTransform(factor=0.01, decimal_places=2)
        # "12345" → "123.45"
    """

    factor: float = 1.0
    decimal_places: int = -1
    default_value: str = ""
    type: str = field(default="scale", init=False)

    def __post_init__(self) -> None:
        self.type = "scale"


@dataclass
class PadTransform(Transform):
    """Pad a source value to a target width without truncating.

    When the source value is already at or longer than *length*, it is returned
    unchanged — pad never truncates.

    Attributes:
        length: Target width in characters.  ``0`` is a no-op.
        pad_char: Character used to fill the padding.  Defaults to ``' '`` (space).
        direction: ``'right'`` pads on the right (RPAD); ``'left'`` pads on the
            left (LPAD).  Defaults to ``'right'``.
        type: Always ``'pad'``.

    Example::

        PadTransform(length=5)                           # RPAD with spaces
        PadTransform(length=5, pad_char='0', direction='left')  # LPAD with zeros
    """

    length: int = 0
    pad_char: str = " "
    direction: str = "right"
    type: str = field(default="pad", init=False)

    def __post_init__(self) -> None:
        self.type = "pad"


@dataclass
class TruncateTransform(Transform):
    """Truncate a source value to at most *length* characters.

    Attributes:
        length: Maximum number of characters to keep.  ``0`` is effectively a
            no-op (the empty string is returned) — used as a sentinel for
            ``"Truncate decimal places"`` style annotations.
        from_end: When ``False`` (default) keep the first ``length`` characters
            (``source[:length]``).  When ``True`` keep the last ``length``
            characters (``source[-length:]``).  If the source is shorter than
            ``length`` it is returned unchanged regardless.
        type: Always ``'truncate'``.

    Example::

        TruncateTransform(length=8)               # keep first 8 chars
        TruncateTransform(length=4, from_end=True) # keep last 4 chars
    """

    length: int = 0
    from_end: bool = False
    type: str = field(default="truncate", init=False)

    def __post_init__(self) -> None:
        self.type = "truncate"


@dataclass
class ConditionalTransform(Transform):
    """Apply one of two transforms depending on whether a condition holds.

    When *condition* evaluates to ``True`` against the current row,
    *then_transform* is applied.  Otherwise *else_transform* is applied.
    Both branches are themselves full ``Transform`` objects, so nested
    conditionals are supported.

    Attributes:
        condition: A condition object to evaluate against the current row.
            Supported types: :class:`NullCheckCondition`,
            :class:`EqualityCondition`, :class:`InCondition`.
        then_transform: ``Transform`` to apply when *condition* is ``True``.
        else_transform: ``Transform`` to apply when *condition* is ``False``.
            Defaults to a noop pass-through (``Transform(type='noop')``).
        type: Always ``'conditional'``.

    Example::

        t = ConditionalTransform(
            condition=NullCheckCondition(field="amount"),
            then_transform=ConstantTransform(value="0"),
            else_transform=DefaultTransform(value="0"),
        )
        apply_transform(None, t, row={"amount": ""})   # -> "0" (then branch)
        apply_transform("99", t, row={"amount": "99"}) # -> "99" (else branch)
    """

    condition: object = field(default=None)
    then_transform: "Transform" = field(default_factory=lambda: Transform(type="noop"))
    else_transform: "Transform" = field(default_factory=lambda: Transform(type="noop"))
    type: str = field(default="conditional", init=False)

    def __post_init__(self) -> None:
        self.type = "conditional"
