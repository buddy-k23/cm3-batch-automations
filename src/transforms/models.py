"""Dataclasses representing parsed field-level transforms.

Each transform type is a lightweight value object.  The base ``Transform``
carries ``type='noop'`` and acts as a pass-through sentinel.  Subclasses add
the parameters specific to their behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
