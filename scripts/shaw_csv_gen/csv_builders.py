"""Mapping- and rules-CSV row builders.

Converts a ``SheetReader`` (from :mod:`scripts.excel_to_spec_text`) into
the row dicts that the bulk converters (``bulk_convert_mappings.py``,
``bulk_convert_rules.py``) expect. Behavior is byte-identical to the
original ``generate_shaw_atoctran_csvs.py``; the only refactoring is
that the column constants and the per-field minimum-length overrides
are now module-level state in this package rather than module-level
state in the ATOCTRAN wrapper.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .helpers import (
    derive_length,
    flatten,
    int_or_blank,
    is_required,
    normalize_data_type,
    to_snake,
    to_str,
    truncate,
)
from .valid_values import extract_valid_values


# --------------------------------------------------------------------------- #
# Workbook column labels (target side of the SOURCE/TARGET banner layout)
# --------------------------------------------------------------------------- #

COL_FIELD_NAME = "Column or Field Name_2"  # second occurrence = target side
COL_DEFINITION = "Definition_2"
COL_POSITION = "Position_2"
COL_DATATYPE = "Datatype_2"
COL_LENGTH = "Length_2"
COL_FORMAT = "Format"
COL_REQUIRED = "Required?"
COL_TRANSFORM = "Transformation Logic"
COL_VALID_VALUES = "Valid Values_2"


# --------------------------------------------------------------------------- #
# Output CSV header schemas
# --------------------------------------------------------------------------- #

MAPPING_HEADERS = [
    "Field Name", "Data Type", "Position", "Length", "Target Name",
    "Required", "Format", "Transformation", "Valid Values", "Description",
]
RULES_HEADERS = [
    "Rule ID", "Rule Name", "Field", "Rule Type", "Severity",
    "Enabled", "Message", "Expected / Values",
]


# --------------------------------------------------------------------------- #
# Per-field minimum trimmed length overrides (operator-confirmed)
# --------------------------------------------------------------------------- #
#
# The spec workbook's ``Length`` column describes the field's fixed-width
# *capacity* — the number of characters reserved at a file offset, padded
# with trailing spaces for variable-length string payloads. For string
# fields the actual payload may be shorter than that capacity (e.g. SHAW
# ships ACCT-NUM as ``"90283924755998    "`` — 14 digits left-justified
# in an 18-char slot). The ``trim`` transformation that
# :class:`~src.config.template_converter.TemplateConverter` adds to every
# field strips the padding before rules execute, so an ``exact_length``
# rule on the spec's Length value rejects every valid row.
#
# This table records the *operator-confirmed* minimum trimmed length for
# string fields whose payload is known to be variable-width. Fields not
# in this table default to :data:`MIN_LENGTH_DEFAULT`. A required field
# always also gets a separate ``not_empty`` rule, so the minimum bound
# is enforced regardless.
STRING_FIELD_MIN_LENGTH_OVERRIDES: Dict[str, int] = {
    "ACCT-NUM": 14,  # SHAW operator-confirmed 2026-05-18 (ATOCTRAN)
    "LN-NUM-ERT": 14,  # SHAW TRANERT account-number field — same shape
}

# Default minimum trimmed length for string fields not otherwise listed.
# Set to 1 so that the rule trivially passes any non-empty payload (the
# ``not_empty`` rule already enforces non-empty when the field is
# required). Setting to 0 would also work but would emit a useless rule.
MIN_LENGTH_DEFAULT: int = 1


# --------------------------------------------------------------------------- #
# Mapping CSV
# --------------------------------------------------------------------------- #


def build_mapping_rows(reader: Any) -> List[Dict[str, str]]:
    """Convert a sheet's data rows to mapping-CSV row dicts.

    Args:
        reader: A :class:`scripts.excel_to_spec_text.SheetReader` for one
            record-type sheet.

    Returns:
        Ordered list of row dicts using :data:`MAPPING_HEADERS` as keys.
    """
    rows: List[Dict[str, str]] = []
    seen_names: Dict[str, int] = {}

    for src in reader.data_rows:
        raw_name = to_str(src.get(COL_FIELD_NAME))
        if not raw_name:
            continue

        # Drop description / metadata rows: a real data row always has a
        # numeric Position. The workbook puts column-definition prose in
        # the row immediately after the header.
        raw_position = src.get(COL_POSITION)
        if int_or_blank(raw_position) == "":
            continue

        # Deduplicate repeated names with _2, _3, ... suffix.
        if raw_name in seen_names:
            seen_names[raw_name] += 1
            field_name = f"{raw_name}_{seen_names[raw_name]}"
        else:
            seen_names[raw_name] = 1
            field_name = raw_name

        raw_datatype = to_str(src.get(COL_DATATYPE))
        raw_format = to_str(src.get(COL_FORMAT))
        data_type = normalize_data_type(raw_datatype, raw_format)

        position = int_or_blank(src.get(COL_POSITION))
        length = derive_length(to_str(src.get(COL_LENGTH)), raw_format)

        required = "Yes" if is_required(src.get(COL_REQUIRED)) else "No"

        transform = to_str(src.get(COL_TRANSFORM))
        wb_valid_values = to_str(src.get(COL_VALID_VALUES))

        # Compute the field's character capacity for the length filter.
        try:
            field_length = int(length) if length else 0
        except ValueError:
            field_length = 0

        valid_values, vv_note = extract_valid_values(
            transform=transform,
            workbook_valid_values=wb_valid_values,
            field_length=field_length,
        )

        description = truncate(to_str(src.get(COL_DEFINITION)), 80)
        if vv_note:
            description = truncate(
                f"{description} [{vv_note}]" if description else f"[{vv_note}]",
                120,
            )

        rows.append({
            "Field Name": field_name,
            "Data Type": data_type,
            "Position": position,
            "Length": length,
            "Target Name": to_snake(raw_name),
            "Required": required,
            "Format": raw_format,
            "Transformation": flatten(transform),
            "Valid Values": valid_values,
            "Description": description,
        })
    return rows


# --------------------------------------------------------------------------- #
# Rules CSV
# --------------------------------------------------------------------------- #


def build_rules_rows(
    mapping_rows: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Generate rules-CSV rows from mapping rows.

    Follows the rule-extraction table in ``prompts/generate-rules-csv.md``.
    Per-row counter starts at R001 for each record type.

    Args:
        mapping_rows: Output of :func:`build_mapping_rows`.

    Returns:
        Ordered list of row dicts using :data:`RULES_HEADERS` as keys.
    """
    rules: List[Dict[str, str]] = []
    n = 0

    def add(field_name: str, rule_name_suffix: str, rule_type: str,
            message: str, expected: str = "", severity: str = "error") -> None:
        nonlocal n
        n += 1
        rules.append({
            "Rule ID": f"R{n:03d}",
            "Rule Name": f"{field_name} {rule_name_suffix}",
            "Field": field_name,
            "Rule Type": rule_type,
            "Severity": severity,
            "Enabled": "Yes",
            "Message": message,
            "Expected / Values": expected,
        })

    for row in mapping_rows:
        field_name = row["Field Name"]
        data_type = row["Data Type"]
        length = row["Length"]
        required = row["Required"]
        valid_values = row["Valid Values"]
        fmt = row["Format"]

        # Skip FILLER fields entirely.
        if field_name.upper().startswith("FILLER"):
            continue

        if required == "Yes":
            add(
                field_name,
                "required",
                "not_empty",
                f"{field_name} must not be empty",
            )

        if data_type == "Numeric":
            add(
                field_name,
                "numeric",
                "numeric",
                f"{field_name} must be numeric",
            )

        if data_type == "Date":
            date_fmt = fmt if fmt else "CCYYMMDD"
            add(
                field_name,
                "date format",
                "date_format",
                f"{field_name} must be a valid date ({date_fmt})",
                expected=date_fmt,
            )

        # Length rule — emit type-aware semantics.
        #
        # For fixed-width files the spec's ``Length`` is the field's
        # character *capacity* (the slot width), not necessarily the
        # payload length. The ``trim`` transformation strips trailing
        # padding before rules execute, so:
        #
        # - **String fields**: the trimmed payload may be shorter than
        #   the slot. Emit a ``length`` rule with ``min..max`` syntax
        #   where ``max`` is the spec Length and ``min`` is either an
        #   operator-confirmed override (see
        #   :data:`STRING_FIELD_MIN_LENGTH_OVERRIDES`) or the generic
        #   :data:`MIN_LENGTH_DEFAULT`. The BA converter's ``length``
        #   rule type lowers to the rule engine's ``length`` operator
        #   with ``min_length`` / ``max_length`` parameters.
        # - **Numeric fields**: payload is zero-padded to the slot
        #   width — ``exact_length`` is correct.
        # - **Date fields**: payload format is exact (e.g. CCYYMMDD =
        #   8 chars) — ``exact_length`` is correct.
        # - **Other / unknown** data types: default to ``exact_length``
        #   for back-compat.
        if required == "Yes" and length:
            try:
                length_int = int(length)
            except (TypeError, ValueError):
                length_int = 0

            if data_type == "String" and length_int > 0:
                min_len = STRING_FIELD_MIN_LENGTH_OVERRIDES.get(
                    field_name.upper(), MIN_LENGTH_DEFAULT
                )
                # Clamp the minimum to the field capacity so a bad
                # override can't produce an impossible range.
                min_len = min(min_len, length_int)
                add(
                    field_name,
                    "length check",
                    "length",
                    f"{field_name} payload length must be between "
                    f"{min_len} and {length_int} characters",
                    expected=f"{min_len}..{length_int}",
                )
            else:
                add(
                    field_name,
                    "length check",
                    "exact_length",
                    f"{field_name} must be exactly {length} characters",
                    expected=length,
                )

        if valid_values:
            add(
                field_name,
                "valid values",
                "valid_values",
                f"{field_name} must be one of: {valid_values}",
                expected=valid_values,
            )

    return rules
