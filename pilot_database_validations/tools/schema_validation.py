#!/usr/bin/env python3
"""JSON Schema validation helpers with optional jsonschema dependency.

Uses jsonschema when available; otherwise falls back to a strict built-in subset
validator that supports all keywords used by this repository schemas.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class SchemaValidationIssue:
    path: str
    message: str


class SchemaValidationError(Exception):
    def __init__(self, schema_name: str, issues: list[SchemaValidationIssue]):
        super().__init__(f"Schema validation failed: {schema_name}")
        self.schema_name = schema_name
        self.issues = issues


def _join_path(base: str, part: str) -> str:
    if not base:
        return part
    if part.startswith("["):
        return f"{base}{part}"
    return f"{base}.{part}"


def _type_ok(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return (isinstance(instance, int) or isinstance(instance, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    return True


def _validate_with_builtin(instance: Any, schema: dict[str, Any], path: str = "") -> list[SchemaValidationIssue]:
    issues: list[SchemaValidationIssue] = []

    schema_type = schema.get("type")
    if schema_type is not None and not _type_ok(instance, schema_type):
        issues.append(SchemaValidationIssue(path or "$", f"expected type '{schema_type}'"))
        return issues

    if "const" in schema and instance != schema["const"]:
        issues.append(SchemaValidationIssue(path or "$", f"must equal const value {schema['const']!r}"))

    if "enum" in schema and instance not in schema["enum"]:
        issues.append(SchemaValidationIssue(path or "$", f"must be one of {schema['enum']}"))

    if isinstance(instance, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(instance) < min_length:
            issues.append(SchemaValidationIssue(path or "$", f"length must be >= {min_length}"))
        max_length = schema.get("maxLength")
        if max_length is not None and len(instance) > max_length:
            issues.append(SchemaValidationIssue(path or "$", f"length must be <= {max_length}"))
        pattern = schema.get("pattern")
        if pattern and re.match(pattern, instance) is None:
            issues.append(SchemaValidationIssue(path or "$", f"must match pattern '{pattern}'"))
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(instance.replace("Z", "+00:00"))
            except ValueError:
                issues.append(SchemaValidationIssue(path or "$", "must be a valid date-time"))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            issues.append(SchemaValidationIssue(path or "$", f"must be >= {minimum}"))

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                issues.append(SchemaValidationIssue(_join_path(path or "$", key), "is required"))

        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        if additional is False:
            for key in instance.keys():
                if key not in properties:
                    issues.append(SchemaValidationIssue(_join_path(path or "$", key), "additional property not allowed"))

        for key, subschema in properties.items():
            if key in instance:
                issues.extend(_validate_with_builtin(instance[key], subschema, _join_path(path or "$", key)))

        if_blocks = schema.get("if")
        then_blocks = schema.get("then")
        if if_blocks and then_blocks:
            if not _validate_with_builtin(instance, if_blocks, path):
                issues.extend(_validate_with_builtin(instance, then_blocks, path))

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < min_items:
            issues.append(SchemaValidationIssue(path or "$", f"must contain at least {min_items} items"))
        item_schema = schema.get("items")
        if item_schema:
            for idx, item in enumerate(instance):
                issues.extend(_validate_with_builtin(item, item_schema, _join_path(path or "$", f"[{idx}]")))

    for subschema in schema.get("allOf", []):
        issues.extend(_validate_with_builtin(instance, subschema, path))

    return issues


def validate_payload(payload: Any, schema_path: Path, schema_name: str) -> None:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    try:
        import jsonschema  # type: ignore

        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        validator = validator_cls(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            issues = []
            for e in errors:
                path = "$"
                for part in e.path:
                    path = _join_path(path, f"[{part}]") if isinstance(part, int) else _join_path(path, str(part))
                issues.append(SchemaValidationIssue(path, e.message))
            raise SchemaValidationError(schema_name, issues)
        return
    except ImportError:
        pass

    issues = _validate_with_builtin(payload, schema, path="$")
    if issues:
        raise SchemaValidationError(schema_name, issues)
