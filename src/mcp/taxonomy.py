"""Live taxonomy introspection for the MCP resources (EF-S3).

This module is the single bridge between the Valdo engine internals and the
MCP ``taxonomy://violations`` / ``taxonomy://rules`` resources. Its purpose
is to keep the agent-facing taxonomy **fresh** without forcing the MCP layer
to maintain a parallel hardcoded copy of every violation kind and every
rule check name.

Design contract
---------------

For each taxonomy the *canonical names* are introspected from the engine
itself; only the one-line human descriptions live in this module. That
split means:

* If an engineer adds a new cross-row check to
  :class:`src.validators.cross_row_validator.CrossRowValidator._DISPATCH`,
  it shows up in ``taxonomy://rules`` automatically — they just need to
  add a one-liner here describing it. The MCP layer does NOT silently
  diverge from the engine.

* If an engineer wires a new violation kind into the SQL-truth comparator
  (``scripts/e2e_lib/db_truth_comparator._KIND_ORDER``), it shows up in
  ``taxonomy://violations`` automatically.

* If the engineer forgets the description, the introspection still
  surfaces the name with a placeholder description, so agents are
  warned rather than silently misled.

Sources introspected
--------------------

* Violation kinds: ``scripts.e2e_lib.db_truth_comparator._KIND_ORDER`` —
  the canonical, deterministic ordering of every kind the SQL-truth
  comparator can emit (``field_mismatch``, ``missing_expected``,
  ``unexpected_file_row``, ``cardinality_violation``,
  ``assertion_failed``, ``unknown_record_type``).

  Note that one additional violation kind is emitted by the multi-record
  validator path (``unknown_record_type`` via
  :meth:`src.validators.multi_record_validator.MultiRecordValidator`)
  but it already overlaps with the SQL-truth ordering above, so the
  single source of truth is correct.

* Cross-row checks: ``CrossRowValidator._DISPATCH``.

* Cross-type checks: ``CrossTypeValidator._DISPATCH``.

* Per-field operators: extracted from
  ``RuleEngine._validate_field`` source via
  :func:`inspect.getsource` + a narrow regex. The operator strings are
  defined as ``elif operator == 'X'`` branches in that method; we scan
  them rather than maintaining a sidecar enum.

The descriptions tabled here are deliberately short (one sentence each)
because the MCP resource is meant to ground an agent's reasoning, not
replace the engine's authoritative docs.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Dict, List

__all__ = [
    "list_violation_taxonomy",
    "list_rule_taxonomy",
]

# ---------------------------------------------------------------------------
# Violation kind descriptions
# ---------------------------------------------------------------------------
#
# Keys MUST match the canonical strings emitted by the engine; values are
# the one-line descriptions surfaced to MCP clients. If a canonical name is
# introspected from the engine but absent here, the taxonomy entry falls
# back to a placeholder description and the missing kind is flagged in the
# resource payload so the gap is visible to operators.
_VIOLATION_DESCRIPTIONS: Dict[str, str] = {
    "field_mismatch": (
        "File value differs from the expected (SQL truth) value for a "
        "specific field on a matched key."
    ),
    "missing_expected": (
        "SQL truth contains a key that has no matching row in the file "
        "under test."
    ),
    "unexpected_file_row": (
        "File under test contains a key that has no matching row in the "
        "SQL truth source."
    ),
    "cardinality_violation": (
        "Number of file rows for a key disagrees with the declared "
        "cardinality of the record type (e.g. expected exactly one "
        "header, found three)."
    ),
    "assertion_failed": (
        "A cross-record-type assertion did not hold (e.g. header count "
        "field did not equal the number of detail rows)."
    ),
    "unknown_record_type": (
        "A file row's discriminator value did not match any configured "
        "record type and the umbrella's default_action allowed it to be "
        "reported."
    ),
}

# ---------------------------------------------------------------------------
# Rule descriptions (cross-row, cross-type, per-field)
# ---------------------------------------------------------------------------

_CROSS_ROW_DESCRIPTIONS: Dict[str, str] = {
    "unique": (
        "No duplicate values are permitted in the named field across the "
        "entire dataset."
    ),
    "unique_composite": (
        "No duplicate combinations are permitted across a tuple of fields."
    ),
    "consistent": (
        "Every row sharing a group key must hold the same value for the "
        "target field."
    ),
    "sequential": (
        "Within each group key, the sequence_field must form a contiguous "
        "1,2,3,... sequence."
    ),
    "group_count": (
        "Actual row count per group key must equal the count declared in "
        "the named count field."
    ),
    "group_sum": (
        "Sum of a numeric field per group key must fall within declared "
        "bounds (or equal a declared target)."
    ),
}

_CROSS_TYPE_DESCRIPTIONS: Dict[str, str] = {
    "required_companion": (
        "If a row of when_type is present, at least one row of "
        "requires_type must also be present."
    ),
    "header_trailer_count": (
        "The trailer's count field must match the actual number of detail "
        "rows in the file."
    ),
    "header_trailer_sum": (
        "The trailer's sum field must match the arithmetic sum of a named "
        "detail field's values."
    ),
    "header_detail_consistent": (
        "The header's field value must match the same field's value on "
        "every detail row."
    ),
    "header_trailer_match": (
        "Header and trailer fields (often paired counters or identifiers) "
        "must hold equal values."
    ),
    "type_sequence": (
        "Record types must appear in the order declared by the umbrella "
        "configuration."
    ),
    "expect_count": (
        "A record-type group must contain exactly N rows (e.g. exactly one "
        "header)."
    ),
}

_PER_FIELD_DESCRIPTIONS: Dict[str, str] = {
    "not_empty": (
        "Field value must be non-empty after trimming (rejects blank, "
        "whitespace-only, and NULL)."
    ),
    "regex": "Field value must match the supplied regular expression pattern.",
    "numeric": "Field value must parse as a number (no non-numeric residue).",
    "date_format": "Field value must parse against the declared date format string.",
    "valid_values": (
        "Field value must appear in the declared list of allowed values "
        "(both sides trimmed before comparison)."
    ),
    "min_value": "Field value must be greater than or equal to the declared minimum.",
    "max_value": "Field value must be less than or equal to the declared maximum.",
    "exact_length": "Field value must have exactly the declared character length.",
    "min_length": "Field value must have at least the declared character length.",
}

# Regex used to extract per-field operator names from the source of
# ``RuleEngine._validate_field``. We deliberately keep this narrow: a
# successful match requires the literal pattern ``operator == '<name>'``.
_PER_FIELD_OPERATOR_RE = re.compile(r"operator\s*==\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]")

# Operator names that are technically dispatched by ``_validate_field``
# but are legacy / low-level building blocks rather than user-facing
# rule types. They are excluded from the public taxonomy so agents see
# only the BA-facing operator names documented in
# ``prompts/generate-rules-csv.md``.
_LEGACY_OPERATORS_TO_HIDE: set[str] = {
    ">", "<", ">=", "<=", "==", "!=",  # raw numeric ops
    "in", "not_in",
    "range",
    "not_null",
    "length",
}


def _introspect_per_field_operators() -> List[str]:
    """Return the BA-facing per-field operator names defined in RuleEngine.

    Reads the source of :meth:`src.validators.rule_engine.RuleEngine._validate_field`
    and extracts every literal compared against ``operator``. The legacy
    raw-operator branches (``>``, ``<``, ``in``, ``range`` etc.) are
    filtered out because they pre-date the BA-friendly rule template and
    are not part of the public rule vocabulary documented for agents.

    Returns:
        Operator names in the order they appear in the source, with
        legacy operators removed and duplicates dropped.
    """
    from src.validators.rule_engine import RuleEngine

    source = inspect.getsource(RuleEngine._validate_field)
    seen: List[str] = []
    for match in _PER_FIELD_OPERATOR_RE.finditer(source):
        name = match.group(1)
        if name in _LEGACY_OPERATORS_TO_HIDE:
            continue
        if name not in seen:
            seen.append(name)
    return seen


def _introspect_violation_kinds() -> List[str]:
    """Return the canonical violation kind names in deterministic order.

    Reads ``scripts.e2e_lib.db_truth_comparator._KIND_ORDER`` — the
    engine's single source of truth for which violation kinds the
    SQL-truth comparator can emit. Importing a single-underscore name
    from a sibling project module is the deliberate trade-off here: we
    refuse to maintain a duplicate enum in the MCP layer.

    Returns:
        Violation kind names sorted by their declared ordinal in
        ``_KIND_ORDER`` (ascending).
    """
    from scripts.e2e_lib.db_truth_comparator import _KIND_ORDER

    return sorted(_KIND_ORDER, key=_KIND_ORDER.__getitem__)


def _introspect_cross_row_checks() -> List[str]:
    """Return cross-row check names registered on the validator's dispatch table."""
    from src.validators.cross_row_validator import CrossRowValidator

    return sorted(CrossRowValidator._DISPATCH)


def _introspect_cross_type_checks() -> List[str]:
    """Return cross-type check names registered on the validator's dispatch table."""
    from src.validators.cross_type_validator import CrossTypeValidator

    return sorted(CrossTypeValidator._DISPATCH)


def _describe(name: str, table: Dict[str, str]) -> str:
    """Look up *name* in *table*; fall back to a clear placeholder if absent.

    The placeholder text is intentionally explicit so an agent reading the
    taxonomy can flag the gap to the operator (rather than silently
    propagating a missing description as if it were truth).
    """
    description = table.get(name)
    if description:
        return description
    return (
        f"(no description registered in src.mcp.taxonomy for '{name}' — "
        "add one when the engine introduces a new entry)"
    )


def list_violation_taxonomy() -> List[Dict[str, Any]]:
    """Return the live violation taxonomy.

    Each entry has the shape::

        {"name": "<canonical kind>", "description": "<one-liner>"}

    Order matches the engine's ``_KIND_ORDER`` so per-record-type kinds
    appear before assertion failures, with unknown-record-type as the
    tail — the same ordering used in reconciliation reports.

    Returns:
        List of taxonomy entries (one per violation kind).
    """
    return [
        {
            "name": name,
            "description": _describe(name, _VIOLATION_DESCRIPTIONS),
        }
        for name in _introspect_violation_kinds()
    ]


def list_rule_taxonomy() -> List[Dict[str, Any]]:
    """Return the live rules taxonomy across all three categories.

    Each entry has the shape::

        {
            "name": "<canonical check / operator>",
            "category": "per-field" | "cross-row" | "cross-type",
            "description": "<one-liner>",
        }

    Categories are emitted in agent-friendly order: per-field first (the
    rules an agent will most often recommend on individual columns),
    then cross-row (within-file aggregate checks), then cross-type
    (multi-record-type / umbrella-level checks).

    Returns:
        List of taxonomy entries (one per rule check).
    """
    entries: List[Dict[str, Any]] = []

    for name in _introspect_per_field_operators():
        entries.append(
            {
                "name": name,
                "category": "per-field",
                "description": _describe(name, _PER_FIELD_DESCRIPTIONS),
            }
        )

    for name in _introspect_cross_row_checks():
        entries.append(
            {
                "name": name,
                "category": "cross-row",
                "description": _describe(name, _CROSS_ROW_DESCRIPTIONS),
            }
        )

    for name in _introspect_cross_type_checks():
        entries.append(
            {
                "name": name,
                "category": "cross-type",
                "description": _describe(name, _CROSS_TYPE_DESCRIPTIONS),
            }
        )

    return entries
