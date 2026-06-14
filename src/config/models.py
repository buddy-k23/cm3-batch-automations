"""Pydantic models for typed config layer — mapping, rules, and workflow configs.

These models replace raw ``dict`` access with validated, typed objects.
All models call ``model_dump()`` to produce dicts that are backward-compatible
with existing code that does ``mapping_config["source"]["format"]`` etc.

Issue #89: Type the config layer with Pydantic models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Shared base
# ---------------------------------------------------------------------------

class _FlexibleModel(BaseModel):
    """Base model that silently ignores unknown extra fields.

    Real mapping / rules JSON files sometimes carry keys that are not part of
    the formal schema (e.g. ``description``, ``source_name``).  Ignoring them
    keeps the models backward-compatible as the JSON schema evolves.
    """

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# SourceConfig
# ---------------------------------------------------------------------------

class SourceConfig(_FlexibleModel):
    """Source file configuration block inside a mapping document.

    Attributes:
        format: File format identifier (e.g. ``pipe_delimited``, ``fixed_width``,
            ``csv``).
        delimiter: Column delimiter character.  Optional — not required for
            ``fixed_width`` format.
        has_header: Whether the source file has a header row.  Defaults to
            ``False``.
        encoding: Character encoding.  Defaults to ``UTF-8``.
    """

    format: str
    delimiter: Optional[str] = None
    has_header: bool = False
    encoding: str = "UTF-8"


# ---------------------------------------------------------------------------
# FieldConfig
# ---------------------------------------------------------------------------

class FieldConfig(_FlexibleModel):
    """A single field specification inside a mapping document.

    Attributes:
        name: Canonical field name used throughout the pipeline.
        data_type: Logical data type string (e.g. ``string``, ``decimal``,
            ``date``, ``integer``).
        required: Whether the field is mandatory.  Defaults to ``False``.
        position: 1-based start position for fixed-width fields.  ``None`` for
            delimited formats.
        length: Character length for fixed-width fields.  ``None`` for
            delimited formats.
        source_name: Column name as it appears in the source file.  Defaults
            to ``name`` when omitted.
        target_name: Column name in the target database / output.  Defaults to
            ``name`` when omitted.
        description: Human-readable description of the field.
        format: Optional format string (e.g. date pattern ``YYYYMMDD``).
        default_value: Value to use when the source field is absent.
        transformations: List of transformation step dicts
            (e.g. ``[{"type": "trim"}]``).
        validation_rules: List of validation rule dicts
            (e.g. ``[{"type": "not_null"}]``).
    """

    name: str
    data_type: str
    required: bool = False
    position: Optional[int] = None
    length: Optional[int] = None
    source_name: Optional[str] = None
    target_name: Optional[str] = None
    description: Optional[str] = None
    format: Optional[str] = None
    default_value: Optional[Any] = None
    transformations: List[Dict[str, Any]] = []
    validation_rules: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# MappingConfig
# ---------------------------------------------------------------------------

class MappingConfig(_FlexibleModel):
    """Complete mapping document configuration.

    This is the top-level model for mapping JSON files consumed by
    ``UniversalMappingParser``, ``EnhancedFileValidator``,
    ``ChunkedFileValidator``, and related services.

    Attributes:
        mapping_name: Unique identifier for this mapping.
        version: Schema version string.  Defaults to ``"unknown"``.
        description: Human-readable description.
        source: Source file configuration (format, delimiter, encoding, …).
        fields: Ordered list of field specifications.  Must be non-empty.
        key_columns: Column names used to uniquely identify a row.
        metadata: Free-form metadata dict (created_by, dates, etc.).
    """

    mapping_name: str
    version: str = "unknown"
    description: Optional[str] = None
    source: SourceConfig
    fields: List[FieldConfig]
    key_columns: List[str] = []
    metadata: Dict[str, Any] = {}

    @field_validator("fields")
    @classmethod
    def fields_must_be_non_empty(cls, v: List[FieldConfig]) -> List[FieldConfig]:
        """Validate that the fields list is not empty.

        Args:
            v: The parsed fields list.

        Returns:
            The unchanged fields list if it is non-empty.

        Raises:
            ValueError: When the list is empty.
        """
        if not v:
            raise ValueError("'fields' must contain at least one entry")
        return v

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MappingConfig":
        """Construct a MappingConfig from a plain dictionary.

        Args:
            data: Raw mapping dict (as loaded from JSON).

        Returns:
            Validated MappingConfig instance.

        Raises:
            pydantic.ValidationError: If required fields are missing or invalid.
        """
        return cls(**data)

    @classmethod
    def from_json(cls, json_text: str) -> "MappingConfig":
        """Construct a MappingConfig from a JSON string.

        Args:
            json_text: Raw JSON text.

        Returns:
            Validated MappingConfig instance.

        Raises:
            json.JSONDecodeError: If the text is not valid JSON.
            pydantic.ValidationError: If the structure does not match the schema.
        """
        return cls(**json.loads(json_text))

    @classmethod
    def from_file(cls, file_path: str) -> "MappingConfig":
        """Load and validate a mapping JSON file.

        Args:
            file_path: Absolute or relative path to the mapping JSON file.

        Returns:
            Validated MappingConfig instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
            pydantic.ValidationError: If the structure does not match the schema.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Mapping file not found: {file_path}")
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(**data)


# ---------------------------------------------------------------------------
# RulesMetadata
# ---------------------------------------------------------------------------

class RulesMetadata(_FlexibleModel):
    """Optional metadata block at the top of a rules config file.

    Attributes:
        name: Display name for the rules set.
        description: Human-readable description.
        created_by: Tool or person that created the rules.
        created_date: ISO-8601 creation timestamp string.
        template_path: Path to the source template if generated.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    created_by: Optional[str] = None
    created_date: Optional[str] = None
    template_path: Optional[str] = None


# ---------------------------------------------------------------------------
# RuleConfig
# ---------------------------------------------------------------------------

class RuleConfig(_FlexibleModel):
    """A single business rule.

    Core required fields match the lowest-common-denominator of the rules JSON
    schema produced by ``RulesTemplateConverter`` and ``BARulesTemplateConverter``.
    Operator-specific fields (``field``, ``value``, ``pattern``, ``min``,
    ``max``, ``values``, ``left_field``, ``right_field``, etc.) are accepted as
    extra fields and round-trip cleanly through ``model_dump()``.

    Attributes:
        id: Unique rule identifier string (e.g. ``"R001"``).
        name: Human-readable rule name.
        type: Rule category — ``"field_validation"`` or ``"cross_field"``.
        severity: Impact level — ``"error"``, ``"warning"``, or ``"info"``.
        operator: Comparison/validation operator (e.g. ``"not_null"``,
            ``"regex"``, ``"range"``, ``"in"``).
        description: Optional rule description.
        field: Target field name for ``field_validation`` rules.
        enabled: Whether the rule is active.  Defaults to ``True``.
    """

    model_config = {"extra": "allow"}  # operator-specific keys must survive

    id: str
    name: str
    type: str
    severity: str
    operator: str
    description: Optional[str] = None
    field: Optional[str] = None
    enabled: bool = True


# ---------------------------------------------------------------------------
# RulesConfig
# ---------------------------------------------------------------------------

class RulesConfig(_FlexibleModel):
    """Top-level rules configuration file model.

    Attributes:
        metadata: Optional metadata block (name, created_by, etc.).
        rules: List of business rules.  May be empty.
    """

    metadata: Optional[RulesMetadata] = None
    rules: List[RuleConfig]

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RulesConfig":
        """Construct a RulesConfig from a plain dictionary.

        Args:
            data: Raw rules dict (as loaded from JSON).

        Returns:
            Validated RulesConfig instance.

        Raises:
            pydantic.ValidationError: If required fields are missing or invalid.
        """
        return cls(**data)

    @classmethod
    def from_json(cls, json_text: str) -> "RulesConfig":
        """Construct a RulesConfig from a JSON string.

        Args:
            json_text: Raw JSON text.

        Returns:
            Validated RulesConfig instance.

        Raises:
            json.JSONDecodeError: If the text is not valid JSON.
            pydantic.ValidationError: If the structure does not match the schema.
        """
        return cls(**json.loads(json_text))

    @classmethod
    def from_file(cls, file_path: str) -> "RulesConfig":
        """Load and validate a rules JSON file.

        Args:
            file_path: Absolute or relative path to the rules JSON file.

        Returns:
            Validated RulesConfig instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            json.JSONDecodeError: If the file is not valid JSON.
            pydantic.ValidationError: If the structure does not match the schema.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Rules file not found: {file_path}")
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls(**data)
