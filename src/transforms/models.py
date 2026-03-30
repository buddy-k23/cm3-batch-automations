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
