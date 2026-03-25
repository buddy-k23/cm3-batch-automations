"""Validators for configuration files (mappings, rules, suites).

Provides functions that check JSON/YAML config files for structural
correctness and required fields.  Each function returns a list of
human-readable error strings — an empty list means the file is valid.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

import yaml


# ---------------------------------------------------------------------------
# Mapping validation
# ---------------------------------------------------------------------------

_MAPPING_REQUIRED_KEYS = {"mapping_name", "version", "source", "target", "fields"}
_FIELD_REQUIRED_KEYS = {"name", "data_type", "target_name"}


def validate_mapping_file(path: Path) -> List[str]:
    """Validate a mapping JSON configuration file.

    Checks that the file is valid JSON and contains the required
    top-level keys (mapping_name, version, source, target, fields).
    Also validates that ``fields`` is a non-empty list with each entry
    containing at minimum ``name``, ``data_type``, and ``target_name``.

    Args:
        path: Path to the mapping JSON file.

    Returns:
        List of error messages.  Empty if the file is valid.
    """
    data, errors = _load_json(path)
    if errors:
        return errors

    errors = _check_required_keys(data, _MAPPING_REQUIRED_KEYS, "mapping")

    # Validate fields array
    fields = data.get("fields")
    if isinstance(fields, list):
        if len(fields) == 0:
            errors.append("Mapping 'fields' array is empty — at least one field is required.")
        for idx, field in enumerate(fields):
            if not isinstance(field, dict):
                errors.append(f"Field at index {idx} is not a JSON object.")
                continue
            missing = _FIELD_REQUIRED_KEYS - field.keys()
            if missing:
                errors.append(
                    f"Field at index {idx} is missing required keys: {sorted(missing)}"
                )

    return errors


# ---------------------------------------------------------------------------
# Rules validation
# ---------------------------------------------------------------------------

_RULES_TOP_KEYS = {"metadata", "rules"}
_METADATA_REQUIRED_KEYS = {"name", "description"}
_RULE_ENTRY_REQUIRED_KEYS = {"id", "name", "type", "severity"}


def validate_rules_file(path: Path) -> List[str]:
    """Validate a rules JSON configuration file.

    Checks that the file is valid JSON with a ``metadata`` object
    (containing ``name`` and ``description``) and a ``rules`` array
    where each entry has at least ``id``, ``name``, ``type``, and
    ``severity``.

    Args:
        path: Path to the rules JSON file.

    Returns:
        List of error messages.  Empty if the file is valid.
    """
    data, errors = _load_json(path)
    if errors:
        return errors

    errors = _check_required_keys(data, _RULES_TOP_KEYS, "rules file")

    # Validate metadata section
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        missing = _METADATA_REQUIRED_KEYS - metadata.keys()
        if missing:
            errors.append(f"Rules metadata is missing required keys: {sorted(missing)}")

    # Validate individual rules
    rules = data.get("rules")
    if isinstance(rules, list):
        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                errors.append(f"Rule at index {idx} is not a JSON object.")
                continue
            missing = _RULE_ENTRY_REQUIRED_KEYS - rule.keys()
            if missing:
                errors.append(
                    f"Rule at index {idx} is missing required keys: {sorted(missing)}"
                )

    return errors


# ---------------------------------------------------------------------------
# Suite validation
# ---------------------------------------------------------------------------

_SUITE_REQUIRED_KEYS = {"name", "steps"}
_STEP_REQUIRED_KEYS = {"name", "type", "file_pattern", "mapping"}


def validate_suite_file(path: Path) -> List[str]:
    """Validate a suite YAML configuration file.

    Checks that the file is valid YAML with required top-level keys
    (``name``, ``steps``) and that each step contains ``name``,
    ``type``, ``file_pattern``, and ``mapping``.

    Args:
        path: Path to the suite YAML file.

    Returns:
        List of error messages.  Empty if the file is valid.
    """
    path = Path(path)
    if not path.exists():
        return [f"Suite file not found: {path}"]

    try:
        text = path.read_text(encoding="utf-8")
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        return [f"YAML parse error in {path.name}: {exc}"]

    if not isinstance(data, dict):
        return [f"Suite file {path.name} does not contain a YAML mapping."]

    errors = _check_required_keys(data, _SUITE_REQUIRED_KEYS, "suite")

    steps = data.get("steps")
    if isinstance(steps, list):
        for idx, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"Step at index {idx} is not a YAML mapping.")
                continue
            missing = _STEP_REQUIRED_KEYS - step.keys()
            if missing:
                errors.append(
                    f"Step at index {idx} is missing required keys: {sorted(missing)}"
                )

    return errors


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> tuple[dict | None, List[str]]:
    """Load and parse a JSON file, returning (data, errors)."""
    path = Path(path)
    if not path.exists():
        return None, [f"File not found: {path}"]
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, [f"JSON parse error in {path.name}: {exc}"]
    if not isinstance(data, dict):
        return None, [f"{path.name} does not contain a JSON object at top level."]
    return data, []


def _check_required_keys(data: dict, required: set[str], label: str) -> List[str]:
    """Return error strings for any missing keys in *data*."""
    missing = required - data.keys()
    if missing:
        return [f"Missing required {label} keys: {sorted(missing)}"]
    return []
