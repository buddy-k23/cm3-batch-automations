"""Condition evaluator for the transform engine.

Provides :func:`evaluate_condition`, which tests a condition object against a
source row dictionary and returns ``True`` when the condition is satisfied.

Supported condition types:

* :class:`~src.transforms.models.NullCheckCondition` — IS NULL / IS NOT NULL
* :class:`~src.transforms.models.EqualityCondition` — field == value / field != value
* :class:`~src.transforms.models.InCondition` — field IN values / field NOT IN values
"""

from __future__ import annotations

from src.transforms.models import EqualityCondition, InCondition, NullCheckCondition


def _is_null(value: object) -> bool:
    """Return True when *value* is considered null.

    A value is null when it is absent (represented here as the sentinel
    ``_MISSING``), ``None``, or a whitespace-only string.

    Args:
        value: The field value extracted from the row dict, or ``None`` if the
            key was absent.

    Returns:
        ``True`` when the value is null; ``False`` otherwise.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def _field_str(row: dict, field_name: str) -> str:
    """Return the stripped string value for *field_name* in *row*.

    Absent keys and ``None`` values are treated as empty strings.

    Args:
        row: A mapping of field names to their string (or ``None``) values.
        field_name: The key to look up.

    Returns:
        The stripped string value, or ``''`` when absent or ``None``.
    """
    raw = row.get(field_name)
    if raw is None:
        return ""
    return str(raw).strip()


def evaluate_condition(
    condition: NullCheckCondition | EqualityCondition | InCondition,
    row: dict,
) -> bool:
    """Evaluate a condition against a data row and return the boolean result.

    Args:
        condition: A condition object describing the check to perform.
            Supported types: :class:`~src.transforms.models.NullCheckCondition`,
            :class:`~src.transforms.models.EqualityCondition`,
            :class:`~src.transforms.models.InCondition`.
        row: A mapping of field names to their string (or ``None``) values
            for the current record.

    Returns:
        ``True`` when the condition is satisfied, ``False`` otherwise.

    Raises:
        TypeError: If *condition* is not a recognised condition type.

    Example::

        cond = NullCheckCondition(field="amount")
        evaluate_condition(cond, {"amount": None})   # True  (IS NULL)
        evaluate_condition(cond, {"amount": "100"})  # False (not null)

        eq = EqualityCondition(field="status", value="ACTIVE")
        evaluate_condition(eq, {"status": "ACTIVE"})   # True
        evaluate_condition(eq, {"status": "INACTIVE"}) # False

        inc = InCondition(field="type", values=["A", "B"])
        evaluate_condition(inc, {"type": "A"})  # True
        evaluate_condition(inc, {"type": "C"})  # False
    """
    if isinstance(condition, NullCheckCondition):
        null = _is_null(row.get(condition.field))
        return (not null) if condition.negate else null

    if isinstance(condition, EqualityCondition):
        field_val = _field_str(row, condition.field)
        cond_val = condition.value.strip() if condition.value else ""
        match = field_val == cond_val
        return (not match) if condition.negate else match

    if isinstance(condition, InCondition):
        field_val = _field_str(row, condition.field)
        stripped_values = [v.strip() if isinstance(v, str) else v for v in condition.values]
        member = field_val in stripped_values
        return (not member) if condition.negate else member

    raise TypeError(
        f"Unsupported condition type: {type(condition).__name__!r}"
    )
