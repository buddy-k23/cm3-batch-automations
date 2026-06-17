"""Service-layer unit tests for logic extracted from files.py (S18-2, #428).

These cover the two blocks moved out of ``src/api/routers/files.py`` so they
are now unit-testable without an HTTP round-trip:

1. ``build_multi_record_validation_response`` — the cross-type-violation
   errors/warnings reshaping previously inlined in ``POST /files/validate``.
2. ``build_connection_override`` — the named-connection / profile /
   individual-field resolution previously inlined in ``POST /files/db-compare``.

The assertions mirror the response contract the router endpoints produced
before the refactor (parity).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.validate_service import build_multi_record_validation_response
from src.services.db_file_compare_service import (
    ALLOWED_DB_ADAPTERS,
    build_connection_override,
)


# ---------------------------------------------------------------------------
# build_multi_record_validation_response — cross-type reshaping
# ---------------------------------------------------------------------------


class TestBuildMultiRecordValidationResponse:
    def test_splits_errors_and_warnings_by_severity(self):
        result = {
            "valid": False,
            "total_rows": 10,
            "cross_type_violations": [
                {"severity": "error", "message": "missing TRL"},
                {"severity": "warning", "message": "odd count"},
                {"severity": "error", "message": "bad sum"},
            ],
        }
        shaped = build_multi_record_validation_response(result)

        assert shaped["valid"] is False
        assert shaped["total_rows"] == 10
        assert shaped["errors"] == [
            {"message": "missing TRL", "severity": "error"},
            {"message": "bad sum", "severity": "error"},
        ]
        assert shaped["warnings"] == [{"message": "odd count", "severity": "warning"}]
        assert shaped["invalid_rows"] == 2  # one per error
        assert shaped["quality_score"] is None
        assert shaped["report_url"] is None

    def test_valid_true_sets_valid_rows_to_total(self):
        result = {"valid": True, "total_rows": 5, "cross_type_violations": []}
        shaped = build_multi_record_validation_response(result)
        assert shaped["valid"] is True
        assert shaped["valid_rows"] == 5
        assert shaped["invalid_rows"] == 0
        assert shaped["errors"] == []
        assert shaped["warnings"] == []

    def test_valid_false_sets_valid_rows_to_zero(self):
        result = {
            "valid": False,
            "total_rows": 5,
            "cross_type_violations": [{"severity": "error", "message": "x"}],
        }
        shaped = build_multi_record_validation_response(result)
        assert shaped["valid_rows"] == 0

    def test_missing_keys_default_safely(self):
        shaped = build_multi_record_validation_response({})
        assert shaped["valid"] is False
        assert shaped["total_rows"] == 0
        assert shaped["valid_rows"] == 0
        assert shaped["invalid_rows"] == 0
        assert shaped["errors"] == []
        assert shaped["warnings"] == []

    def test_violation_missing_fields_get_defaults(self):
        result = {
            "valid": False,
            "total_rows": 1,
            "cross_type_violations": [{"severity": "error"}],
        }
        shaped = build_multi_record_validation_response(result)
        assert shaped["errors"] == [{"message": "", "severity": "error"}]


# ---------------------------------------------------------------------------
# build_connection_override — named / profile / individual resolution
# ---------------------------------------------------------------------------


def _named(adapter="oracle"):
    return SimpleNamespace(
        host="stg:1522/DB",
        user="Valdo",
        password="secret",
        schema="APP_INT",
        adapter=adapter,
    )


def _profile():
    return SimpleNamespace(
        dsn="profhost:1521/SVC",
        user="PROFUSER",
        password="PROFPW",
        schema="PROFSCH",
        db_adapter="oracle",
    )


class TestBuildConnectionOverride:
    def test_no_inputs_returns_none(self):
        assert build_connection_override() is None

    def test_named_connection_fields_used(self):
        override = build_connection_override(named_connection=_named())
        assert override == {
            "db_host": "stg:1522/DB",
            "db_user": "Valdo",
            "db_password": "secret",
            "db_schema": "APP_INT",
            "db_adapter": "oracle",
        }

    def test_named_connection_overrides_individual_fields(self):
        override = build_connection_override(
            named_connection=_named(),
            db_host="ignored:1521/X",
            db_user="ignored",
        )
        assert override["db_host"] == "stg:1522/DB"
        assert override["db_user"] == "Valdo"

    def test_profile_config_used_and_individual_ignored(self):
        override = build_connection_override(
            profile_config=_profile(),
            db_host="ignored",
            db_user="ignored",
        )
        assert override == {
            "db_host": "profhost:1521/SVC",
            "db_user": "PROFUSER",
            "db_password": "PROFPW",
            "db_schema": "PROFSCH",
            "db_adapter": "oracle",
        }

    def test_individual_fields_filtered_to_non_none(self):
        override = build_connection_override(
            db_host="h:1521/F", db_user="u", db_password="p", db_adapter="oracle"
        )
        assert override == {
            "db_host": "h:1521/F",
            "db_user": "u",
            "db_password": "p",
            "db_adapter": "oracle",
        }
        assert "db_schema" not in override  # None is dropped

    def test_adapter_only_override_is_built(self):
        override = build_connection_override(db_adapter="sqlite")
        assert override == {"db_adapter": "sqlite"}

    def test_invalid_individual_adapter_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid db_adapter 'mysql'"):
            build_connection_override(db_adapter="mysql")

    def test_invalid_named_adapter_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid db_adapter"):
            build_connection_override(named_connection=_named(adapter="mysql"))

    def test_profile_adapter_not_revalidated(self):
        # Profile adapter is trusted upstream; even a non-standard value passes.
        prof = SimpleNamespace(
            dsn="d", user="u", password="p", schema="s", db_adapter="something"
        )
        override = build_connection_override(profile_config=prof)
        assert override["db_adapter"] == "something"

    def test_allowed_adapters_constant(self):
        assert ALLOWED_DB_ADAPTERS == frozenset({"oracle", "postgresql", "sqlite"})
