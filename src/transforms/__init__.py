"""Transform system for field-level data transformations.

Provides a parser that converts free-text mapping descriptions into typed
``Transform`` objects, and an engine that applies those objects to source
field values.  Phase 3a adds condition models and an evaluator; Phase 3b
extends conditions with equality and multi-value (IN) checks.  Phase 3c
adds ``ConditionalTransform`` for IF/ELSE dispatch.  Phase 3e adds
``SequentialNumberTransform`` and ``SequentialCounter`` for stateful
row-counter tracking.  Phase 4a adds ``DateFormatTransform`` for date
string reformatting.  Phase 4b adds ``NumericFormatTransform`` for
signed zero-padded numeric output.  Phase 4c adds ``ScaleTransform`` for
multiply/divide numeric scaling.  Phase 4d adds ``PadTransform`` and
``TruncateTransform`` for explicit padding and truncation operations.

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
``DateFormatTransform``
    Convert a date string from one strptime format to another strftime format.
``NullCheckCondition``
    Condition that tests whether a named field is null/blank (or IS NOT NULL
    when ``negate=True``).
``EqualityCondition``
    Condition that tests whether a named field equals a given value (or !=
    when ``negate=True``).
``InCondition``
    Condition that tests whether a named field's value is a member of a list
    (or NOT IN when ``negate=True``).
``ConditionalTransform``
    Apply then_transform or else_transform depending on a condition.
``SequentialNumberTransform``
    Assign an incrementing sequence number to each processed record.
``SequentialCounter``
    Stateful counter manager for ``SequentialNumberTransform`` instances.
``NumericFormatTransform``
    Zero-pad a numeric value to a fixed width with optional sign prefix.
``ScaleTransform``
    Multiply a numeric source value by a fixed factor (supports divide via
    fractional factors).
``PadTransform``
    Pad a source value to a target width (LPAD/RPAD) without truncating.
``TruncateTransform``
    Truncate a source value to at most N characters.
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
from src.transforms.multi_record_transform_engine import MultiRecordTransformEngine
from src.transforms.sequential_counter import SequentialCounter
from src.transforms.transform_engine import apply_transform
from src.transforms.transform_mismatch_reporter import TransformMismatchReporter
from src.transforms.transform_orchestrator import TransformEngine
from src.transforms.transform_parser import parse_transform

__all__ = [
    "Transform",
    "DefaultTransform",
    "BlankTransform",
    "ConstantTransform",
    "ConcatPart",
    "ConcatTransform",
    "FieldMapTransform",
    "DateFormatTransform",
    "NullCheckCondition",
    "EqualityCondition",
    "InCondition",
    "ConditionalTransform",
    "SequentialNumberTransform",
    "SequentialCounter",
    "NumericFormatTransform",
    "ScaleTransform",
    "PadTransform",
    "TruncateTransform",
    "TransformEngine",
    "MultiRecordTransformEngine",
    "TransformMismatchReporter",
    "parse_transform",
    "apply_transform",
    "evaluate_condition",
]
