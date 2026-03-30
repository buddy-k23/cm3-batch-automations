"""Transform system for field-level data transformations.

Provides a parser that converts free-text mapping descriptions into typed
``Transform`` objects, and an engine that applies those objects to source
field values.  Phase 3a adds condition models and an evaluator; Phase 3b
extends conditions with equality and multi-value (IN) checks.

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
``NullCheckCondition``
    Condition that tests whether a named field is null/blank (or IS NOT NULL
    when ``negate=True``).
``EqualityCondition``
    Condition that tests whether a named field equals a given value (or !=
    when ``negate=True``).
``InCondition``
    Condition that tests whether a named field's value is a member of a list
    (or NOT IN when ``negate=True``).
``parse_transform``
    Parse a free-text transform description into a typed ``Transform``.
``apply_transform``
    Apply a ``Transform`` to a source value, returning the output string.
``evaluate_condition``
    Evaluate a condition object against a row dict, returning ``bool``.
"""

from src.transforms.condition_evaluator import evaluate_condition
from src.transforms.models import (
    BlankTransform,
    ConcatPart,
    ConcatTransform,
    ConstantTransform,
    DefaultTransform,
    EqualityCondition,
    FieldMapTransform,
    InCondition,
    NullCheckCondition,
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
    "NullCheckCondition",
    "EqualityCondition",
    "InCondition",
    "parse_transform",
    "apply_transform",
    "evaluate_condition",
]
