"""Unit tests for the dialect-free mapping-type -> CanonicalType matrix.

Covers ADR 0022 S12-1b §3: the compatibility matrix that replaced the
Oracle-only ``_types_compatible`` string list, and the boolean/UNKNOWN
dialect-neutral advisory rules.
"""

from __future__ import annotations

import pytest

from src.database.adapters.base import CanonicalType
from src.database.reconciliation import (
    MAPPING_TYPE_COMPATIBILITY,
    canonical_compatible,
    is_advisory,
)


@pytest.mark.parametrize(
    "mapping_type,canonical,expected",
    [
        # string
        ("string", CanonicalType.STRING, True),
        ("string", CanonicalType.INTEGER, False),
        # integer
        ("integer", CanonicalType.INTEGER, True),
        ("integer", CanonicalType.DECIMAL, False),
        ("integer", CanonicalType.STRING, False),
        # number (broad numeric)
        ("number", CanonicalType.INTEGER, True),
        ("number", CanonicalType.DECIMAL, True),
        ("number", CanonicalType.FLOAT, True),
        ("number", CanonicalType.STRING, False),
        # decimal
        ("decimal", CanonicalType.DECIMAL, True),
        ("decimal", CanonicalType.FLOAT, True),
        ("decimal", CanonicalType.INTEGER, False),
        # date
        ("date", CanonicalType.DATE, True),
        ("date", CanonicalType.TIMESTAMP, True),
        ("date", CanonicalType.STRING, False),
        # boolean: native + the two dialect-neutral advisory carriers
        ("boolean", CanonicalType.BOOLEAN, True),
        ("boolean", CanonicalType.INTEGER, True),
        ("boolean", CanonicalType.STRING, True),
        ("boolean", CanonicalType.DECIMAL, False),
    ],
)
def test_matrix_cells(mapping_type, canonical, expected):
    assert canonical_compatible(mapping_type, canonical) is expected


def test_unknown_is_compatible_with_everything():
    """UNKNOWN (typeless SQLite) is compatible with every mapping type."""
    for mapping_type in MAPPING_TYPE_COMPATIBILITY:
        assert canonical_compatible(mapping_type, CanonicalType.UNKNOWN) is True


def test_unknown_mapping_type_is_permissive():
    """An unrecognised mapping type does not raise and is non-blocking."""
    # Unknown mapping vocab should not hard-fail reconciliation.
    assert canonical_compatible("mystery", CanonicalType.STRING) is True


def test_boolean_over_native_is_not_advisory():
    """PostgreSQL native boolean is an exact match — no advisory."""
    assert is_advisory("boolean", CanonicalType.BOOLEAN) is False


@pytest.mark.parametrize("carrier", [CanonicalType.INTEGER, CanonicalType.STRING])
def test_boolean_over_non_native_is_advisory(carrier):
    """Oracle NUMBER(1)/CHAR(1) and SQLite INTEGER booleans are advisory."""
    assert is_advisory("boolean", carrier) is True


def test_unknown_is_advisory():
    """UNKNOWN compatibility carries an informational note, not a silent pass."""
    assert is_advisory("string", CanonicalType.UNKNOWN) is True
