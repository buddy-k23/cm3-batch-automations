"""Pydantic configuration models for multi-record-type file validation.

Supports files that contain multiple record types identified by a discriminator
field at a fixed position (e.g. header/detail/trailer batch files).
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, field_validator


class DiscriminatorConfig(BaseModel):
    """Configuration for the field that identifies the record type.

    The discriminator is a fixed-width substring extracted from each line
    using 1-indexed ``position`` and ``length``.

    Attributes:
        field: Logical name for the discriminator field (used in messages).
        position: 1-indexed start position of the discriminator in each line.
        length: Number of characters to read from ``position``.
    """

    field: str
    position: int
    length: int

    @field_validator("position")
    @classmethod
    def position_must_be_positive(cls, v: int) -> int:
        """Validate that position is >= 1.

        Args:
            v: The position value.

        Returns:
            The validated position.

        Raises:
            ValueError: When position is less than 1.
        """
        if v < 1:
            raise ValueError("position must be >= 1 (1-indexed)")
        return v

    @field_validator("length")
    @classmethod
    def length_must_be_positive(cls, v: int) -> int:
        """Validate that length is >= 1.

        Args:
            v: The length value.

        Returns:
            The validated length.

        Raises:
            ValueError: When length is less than 1.
        """
        if v < 1:
            raise ValueError("length must be >= 1")
        return v


class RecordTypeConfig(BaseModel):
    """Configuration for a single record type within a multi-record file.

    Attributes:
        match: Discriminator value that identifies this record type (e.g. "HDR").
            Leave empty when using ``position`` instead.
        position: If "first", this type matches the first row.
            If "last", this type matches the last row.
            Leave empty to use ``match`` exclusively.
        mapping: Path to the mapping JSON file for this record type.
        rules: Path to the rules JSON file for this record type (optional).
        expect: Cardinality expectation.  One of:
            ``exactly_one`` — exactly one row of this type must appear.
            ``at_least_one`` — at least one row must appear.
            ``any`` — any number (including zero) is acceptable.
    """

    match: str = ""
    position: str = ""
    mapping: str
    rules: str = ""
    expect: str = "any"


class CrossTypeRule(BaseModel):
    """Configuration for a single cross-record-type validation rule.

    Attributes:
        check: Rule type name.  One of:
            ``required_companion``, ``header_trailer_count``,
            ``header_trailer_sum``, ``header_detail_consistent``,
            ``header_trailer_match``, ``type_sequence``, ``expect_count``.
        when_type: For ``required_companion`` — record type that triggers the check.
        requires_type: For ``required_companion`` — record type that must also exist.
        key_field: Key field name (reserved for future use).
        trailer_field: Field name in the trailer / record_type row.
        header_field: Field name in the header row.
        detail_field: Field name in the detail row.
        sum_field: Field name to sum (alias; ``sum_of`` supersedes this).
        count_of: For ``header_trailer_count`` — which record type group to count.
            Defaults to ``"detail"``.
        sum_of: List of field names to sum for ``header_trailer_sum``.
        expected_order: Ordered list of record type keys for ``type_sequence``.
        record_type: Record type key targeted by this rule (for ``header_trailer_count``,
            ``header_trailer_sum``, ``expect_count``).
        exactly: Expected exact row count for ``expect_count``.  -1 means unchecked.
        strict: When True, partial-order violations are errors (reserved for future use).
        message: Custom violation message template.
        severity: Violation severity.  One of ``"error"`` or ``"warning"``.
    """

    check: str
    when_type: str = ""
    requires_type: str = ""
    key_field: str = ""
    trailer_field: str = ""
    header_field: str = ""
    detail_field: str = ""
    sum_field: str = ""
    count_of: str = "detail"
    sum_of: List[str] = []
    expected_order: List[str] = []
    record_type: str = ""
    exactly: int = -1
    strict: bool = False
    message: str = ""
    severity: str = "error"


class MultiRecordConfig(BaseModel):
    """Top-level configuration for multi-record-type file validation.

    Attributes:
        discriminator: How to extract the record-type identifier from each line.
        record_types: Mapping from logical type name (e.g. ``"header"``) to its
            :class:`RecordTypeConfig`.
        cross_type_rules: List of :class:`CrossTypeRule` applied after per-type
            validation.
        default_action: Action to take when a line's discriminator does not match
            any configured record type.  One of:
            ``"warn"`` — emit a warning violation.
            ``"error"`` — emit an error violation.
            ``"skip"`` — silently discard the row.
    """

    discriminator: DiscriminatorConfig
    record_types: dict
    cross_type_rules: List[CrossTypeRule] = []
    default_action: str = "warn"
