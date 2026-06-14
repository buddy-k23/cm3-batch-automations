"""Normalization helpers for fixed-width multi-record-type mappings (v2)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Tuple


def normalize_fixed_width_mapping_v2(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize legacy fixed-width mappings to the internal v2 record-types contract.

    - If mapping already has `record_types`, it is treated as v2 and defaults are filled.
    - If mapping is legacy fixed-width (`source.format=fixed_width` + `fields`), it is
      normalized to a single `DETAIL` record type with `classification.kind=default`.
    - Non-fixed-width mappings are returned unchanged.
    """

    normalized = deepcopy(mapping)

    # Already v2-like
    if isinstance(normalized.get("record_types"), list):
        _ensure_v2_defaults(normalized)
        return normalized

    source_format = (normalized.get("source") or {}).get("format")
    has_fields = isinstance(normalized.get("fields"), list)
    if source_format != "fixed_width" or not has_fields:
        return normalized

    fields = normalized.get("fields", [])
    expected_total_width = normalized.get("total_record_length")
    if expected_total_width is None:
        expected_total_width = sum(int(f.get("length", 0) or 0) for f in fields)

    normalized.setdefault("format", "fixed_width")
    normalized["record_types"] = [
        {
            "id": "DETAIL",
            "description": "Normalized from legacy fixed-width mapping",
            "classification": {"kind": "default"},
            "expected_total_width": expected_total_width,
            "fields": fields,
        }
    ]

    normalized.setdefault(
        "file_rules",
        {
            "required_record_types": [],
            "sequence_rules": [],
            "reconciliation_rules": [],
            "unknown_record_policy": "error",
        },
    )

    metadata = normalized.setdefault("metadata", {})
    metadata.setdefault("source_mapping_version", "legacy")

    _ensure_v2_defaults(normalized)
    return normalized


def validate_fixed_width_mapping_v2(mapping: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Minimal validation for v2 fixed-width mapping structure."""

    errors: List[str] = []

    record_types = mapping.get("record_types")
    if not isinstance(record_types, list) or not record_types:
        return False, ["Invalid mapping: 'record_types' must be a non-empty list."]

    ids = []
    default_count = 0
    for idx, rt in enumerate(record_types):
        rt_id = rt.get("id")
        if not rt_id:
            errors.append(f"Invalid mapping: record_types[{idx}] is missing required 'id'.")
        else:
            ids.append(rt_id)

        fields = rt.get("fields")
        if not isinstance(fields, list):
            errors.append(
                f"Invalid mapping: record type '{rt_id or idx}' must define 'fields' as a list."
            )

        classification = rt.get("classification", {})
        kind = classification.get("kind")
        if kind not in {"discriminator", "length", "default"}:
            errors.append(
                f"Invalid mapping: record type '{rt_id or idx}' has unsupported classification kind '{kind}'."
            )
        if kind == "default":
            default_count += 1

    duplicates = sorted({x for x in ids if ids.count(x) > 1})
    if duplicates:
        errors.append(f"Invalid mapping: duplicate record type ids: {duplicates}.")

    if default_count > 1:
        errors.append("Invalid mapping: only one record type may use classification kind 'default'.")

    return len(errors) == 0, errors


def _ensure_v2_defaults(mapping: Dict[str, Any]) -> None:
    mapping.setdefault("format", "fixed_width")
    mapping.setdefault("version", "2.0")

    file_rules = mapping.setdefault("file_rules", {})
    file_rules.setdefault("required_record_types", [])
    file_rules.setdefault("sequence_rules", [])
    file_rules.setdefault("reconciliation_rules", [])
    file_rules.setdefault("unknown_record_policy", "error")

    metadata = mapping.setdefault("metadata", {})
    metadata.setdefault("source_mapping_version", "v2")
