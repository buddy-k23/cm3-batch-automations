"""Unit tests for reconcile_mapping_service + helpers (S17-4, #432).

The pure verdict-projection helpers are tested directly; the orchestrator is
tested with the DB layer (loader/parser/adapter/reconciler) mocked so no live
database is required.
"""

from unittest import mock

import pytest

from src.services import reconcile_service as rs
from src.services.reconcile_service import (
    ReconcileServiceError,
    reconcile_mapping_service,
)


class TestClassifyWarning:
    def test_mismatch(self):
        assert rs._classify_warning("Column X: type mismatch (NUMBER vs VARCHAR)") == "mismatch"

    def test_advisory(self):
        assert rs._classify_warning("SQLite has no native boolean type") == "advisory"
        assert rs._classify_warning("type could not be determined for Y") == "advisory"

    def test_other(self):
        assert rs._classify_warning("something else entirely") == "other"


class TestDeriveStatus:
    def test_error_wins(self):
        assert rs._derive_status(1, 1, 1) == "error"

    def test_mismatch_next(self):
        assert rs._derive_status(0, 2, 1) == "mismatch"

    def test_advisories_next(self):
        assert rs._derive_status(0, 0, 3) == "advisories"

    def test_clean(self):
        assert rs._derive_status(0, 0, 0) == "clean"


class TestLooksLikePath:
    def test_path_indicators(self):
        assert rs._looks_like_path("config/mappings/m.json") is True
        assert rs._looks_like_path("dir\\m") is True
        assert rs._looks_like_path("m.json") is True

    def test_bare_id(self):
        assert rs._looks_like_path("atoctran") is False


class TestQualifyTable:
    def test_none_table(self):
        assert rs._qualify_table(None, "SCH") is None

    def test_prepends_schema(self):
        assert rs._qualify_table("T", "SCH") == "SCH.T"

    def test_already_qualified_unchanged(self):
        assert rs._qualify_table("SCH.T", "OTHER") == "SCH.T"

    def test_no_schema_unchanged(self):
        assert rs._qualify_table("T", None) == "T"


class TestProjectVerdict:
    def test_splits_warnings_into_buckets(self):
        result = {
            "valid": True,
            "errors": [],
            "warnings": [
                "Column A: type mismatch X vs Y",
                "no native boolean for B",
                "harmless note",
            ],
            "mapped_columns": 3,
            "database_columns": 4,
            "unmapped_required": ["req1"],
        }
        verdict = rs._project_verdict(
            result, mapping_name="m", table="T", schema="S", db_adapter="SQLiteAdapter"
        )
        assert verdict["status"] == "mismatch"
        assert verdict["summary"]["mismatch_count"] == 1
        assert verdict["summary"]["advisory_count"] == 1
        assert verdict["summary"]["warning_count"] == 3
        assert verdict["mismatches"] == ["Column A: type mismatch X vs Y"]
        assert verdict["advisories"] == ["no native boolean for B"]
        assert verdict["unmapped_required"] == ["req1"]
        assert verdict["db_adapter"] == "SQLiteAdapter"

    def test_clean_verdict(self):
        verdict = rs._project_verdict(
            {"valid": True, "errors": [], "warnings": []},
            mapping_name="m",
            table=None,
            schema=None,
            db_adapter="X",
        )
        assert verdict["status"] == "clean"
        assert verdict["valid"] is True

    def test_error_verdict(self):
        verdict = rs._project_verdict(
            {"valid": False, "errors": ["missing table FOO"], "warnings": []},
            mapping_name="m",
            table="FOO",
            schema=None,
            db_adapter="X",
        )
        assert verdict["status"] == "error"
        assert verdict["summary"]["error_count"] == 1


class TestReconcileMappingServiceOrchestration:
    def test_rejects_empty_mapping(self):
        with pytest.raises(ReconcileServiceError, match="mapping is required"):
            reconcile_mapping_service("")

    def test_missing_mapping_file_raises_service_error(self):
        with mock.patch.object(
            rs, "_load_mapping", side_effect=FileNotFoundError("nope")
        ), mock.patch.object(rs, "ConfigLoader"):
            with pytest.raises(ReconcileServiceError, match="nope"):
                reconcile_mapping_service("ghost")

    def test_happy_path_returns_verdict(self):
        mapping_doc = mock.Mock()
        mapping_doc.target = {"type": "database", "table_name": "T"}
        mapping_doc.mapping_name = "mymap"

        reconciler = mock.Mock()
        reconciler.reconcile_mapping.return_value = {
            "valid": True,
            "errors": [],
            "warnings": [],
            "mapped_columns": 2,
            "database_columns": 2,
        }
        adapter = mock.Mock()

        with mock.patch.object(rs, "ConfigLoader"), mock.patch.object(
            rs, "_load_mapping", return_value={"raw": "dict"}
        ), mock.patch.object(rs, "MappingParser") as parser_cls, mock.patch.object(
            rs, "get_database_adapter", return_value=adapter
        ), mock.patch.object(
            rs, "SchemaReconciler", return_value=reconciler
        ):
            parser_cls.return_value.parse.return_value = mapping_doc
            verdict = reconcile_mapping_service("mymap")

        assert verdict["status"] == "clean"
        assert verdict["mapping_name"] == "mymap"
        assert verdict["table"] == "T"

    def test_database_target_without_table_raises(self):
        mapping_doc = mock.Mock()
        mapping_doc.target = {"type": "database"}  # no table_name
        mapping_doc.mapping_name = "m"

        with mock.patch.object(rs, "ConfigLoader"), mock.patch.object(
            rs, "_load_mapping", return_value={"raw": "dict"}
        ), mock.patch.object(rs, "MappingParser") as parser_cls:
            parser_cls.return_value.parse.return_value = mapping_doc
            with pytest.raises(ReconcileServiceError, match="No target table"):
                reconcile_mapping_service("m")

    def test_parse_failure_raises_service_error(self):
        with mock.patch.object(rs, "ConfigLoader"), mock.patch.object(
            rs, "_load_mapping", return_value={"raw": "dict"}
        ), mock.patch.object(rs, "MappingParser") as parser_cls:
            parser_cls.return_value.parse.side_effect = ValueError("bad mapping")
            with pytest.raises(ReconcileServiceError, match="Failed to parse"):
                reconcile_mapping_service("m")
