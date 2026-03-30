"""Transform system for field-level data transformations.

Provides a parser that converts free-text mapping descriptions into typed
``Transform`` objects, and an engine that applies those objects to source
field values.

Exported names
--------------
``Transform``
    Base pass-through (noop) transform.
``DefaultTransform``
    Use source value when present; fall back to a configured default.
``BlankTransform``
    Always output blank / space-filled value.
``ConstantTransform``
    Always output a fixed constant, ignoring the source.
``ConcatPart``
    Single field reference (with optional LPAD) inside a ``ConcatTransform``.
``ConcatTransform``
    Concatenate multiple source fields, with optional per-field left-padding.
``FieldMapTransform``
    Map a named source field directly to the target field.
``parse_transform``
    Parse a free-text transform description into a typed ``Transform``.
``apply_transform``
    Apply a ``Transform`` to a source value, returning the output string.
"""

from src.transforms.models import (
    BlankTransform,
    ConcatPart,
    ConcatTransform,
    ConstantTransform,
    DefaultTransform,
    FieldMapTransform,
    Transform,
)
from src.transforms.transform_engine import apply_transform
from src.transforms.transform_parser import parse_transform

__all__ = [
    "Transform",
    "DefaultTransform",
    "BlankTransform",
    "ConstantTransform",
    "ConcatPart",
    "ConcatTransform",
    "FieldMapTransform",
    "parse_transform",
    "apply_transform",
]
