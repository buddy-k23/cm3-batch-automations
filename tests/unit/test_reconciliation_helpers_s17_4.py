"""Unit tests for reconciliation pure helpers + report/validate-all (S17-4, #432).

Complements test_reconciliation.py (which drives reconcile_mapping end-to-end)
by covering the dialect-free type helpers, the date-format / decimal-precision
compatibility checks, the human-readable report renderer, and the
MappingValidator multi-mapping aggregator — all without a live database.
"""

from src.config.mapping_parser import MappingParser
from src.database.adapters.base import CanonicalType
from src.database.reconciliation import (
    MappingValidator,
    SchemaReconciler,
    _raw_name_to_canonical,
    canonical_compatible,
    is_advisory,
)


class _DummyAdapter:
    pass


def _mapping(data_type="string", validation_rules=None, fmt=None):
    col = {
        "source_column": "name",
        "target_column": "NAME",
        "data_type": data_type,
        "required": True,
        "transformations": [],
        "validation_rules": validation_rules or [],
    }
    if fmt is not None:
        col["format"] = fmt
    return MappingParser().parse(
        {
            "mapping_name": "m",
            "version": "1.0.0",
            "description": "d",
            "source": {"type": "file", "format": "pipe_delimited"},
            "target": {"type": "database", "table_name": "T"},
            "mappings": [col],
            "key_columns": ["name"],
        }
    )


class TestRawNameToCanonical:
    def test_known_names(self):
        assert _raw_name_to_canonical("VARCHAR2") is CanonicalType.STRING
        assert _raw_name_to_canonical("integer") is CanonicalType.INTEGER
        assert _raw_name_to_canonical("NUMERIC") is CanonicalType.DECIMAL
        assert _raw_name_to_canonical("DOUBLE PRECISION") is CanonicalType.FLOAT
        assert _raw_name_to_canonical("BOOLEAN") is CanonicalType.BOOLEAN
        assert _raw_name_to_canonical("DATE") is CanonicalType.DATE

    def test_number_resolved_by_scale(self):
        assert _raw_name_to_canonical("NUMBER", scale=0) is CanonicalType.INTEGER
        assert _raw_name_to_canonical("NUMBER", scale=2) is CanonicalType.DECIMAL
        assert _raw_name_to_canonical("NUMBER") is CanonicalType.DECIMAL

    def test_timestamp_prefix(self):
        assert _raw_name_to_canonical("TIMESTAMP(6)") is CanonicalType.TIMESTAMP

    def test_empty_and_unknown(self):
        assert _raw_name_to_canonical("") is CanonicalType.UNKNOWN
        assert _raw_name_to_canonical(None) is CanonicalType.UNKNOWN
        assert _raw_name_to_canonical("MADE_UP") is CanonicalType.UNKNOWN


class TestCanonicalCompatible:
    def test_unknown_is_compatible_with_all(self):
        assert canonical_compatible("string", CanonicalType.UNKNOWN) is True

    def test_string_matches_string(self):
        assert canonical_compatible("string", CanonicalType.STRING) is True

    def test_string_against_integer_is_conflict(self):
        assert canonical_compatible("string", CanonicalType.INTEGER) is False

    def test_unrecognised_mapping_type_non_blocking(self):
        assert canonical_compatible("weirdtype", CanonicalType.INTEGER) is True

    def test_number_accepts_integer_and_decimal(self):
        assert canonical_compatible("number", CanonicalType.INTEGER) is True
        assert canonical_compatible("number", CanonicalType.DECIMAL) is True


class TestIsAdvisory:
    def test_unknown_is_advisory(self):
        assert is_advisory("string", CanonicalType.UNKNOWN) is True

    def test_boolean_on_non_native_is_advisory(self):
        assert is_advisory("boolean", CanonicalType.INTEGER) is True

    def test_boolean_on_native_not_advisory(self):
        assert is_advisory("boolean", CanonicalType.BOOLEAN) is False

    def test_exact_match_not_advisory(self):
        assert is_advisory("string", CanonicalType.STRING) is False


class TestTypesCompatible:
    def test_delegates_to_canonical(self):
        rec = SchemaReconciler(_DummyAdapter())
        assert rec._types_compatible("string", "VARCHAR2") is True
        assert rec._types_compatible("string", "NUMBER") is False


class TestDateFormatCompatibility:
    def test_no_date_hint_returns_none(self):
        rec = SchemaReconciler(_DummyAdapter())
        mapping = _mapping(data_type="string")
        col = mapping.mappings[0]
        assert rec._check_date_format_compatibility("NAME", col, "VARCHAR2") is None

    def test_date_mapping_on_non_date_db_type_warns(self):
        rec = SchemaReconciler(_DummyAdapter())
        col = _mapping(data_type="date").mappings[0]
        msg = rec._check_date_format_compatibility("NAME", col, "VARCHAR2")
        assert msg and "date" in msg

    def test_time_format_on_date_column_informational(self):
        rec = SchemaReconciler(_DummyAdapter())
        col = _mapping(
            data_type="date",
            validation_rules=[{"type": "date_format", "parameters": {"format": "YYYYMMDD HH:MI:SS"}}],
        ).mappings[0]
        msg = rec._check_date_format_compatibility("NAME", col, "DATE")
        assert msg and "time components" in msg


class TestGenerateReport:
    def _reconciler_with(self, columns, details, required=None):
        rec = SchemaReconciler(_DummyAdapter())
        rec._table_exists = lambda _t, _o=None: True
        rec._get_table_columns = lambda _t, _o=None: columns
        rec._get_required_columns = lambda _t, _o=None: required or set()
        rec._get_column_details = lambda _t, _o=None: details
        return rec

    def test_report_valid_no_issues(self):
        rec = self._reconciler_with(
            ["NAME"],
            {
                "NAME": {
                    "data_type": "VARCHAR2",
                    "data_length": 100,
                    "data_precision": None,
                    "data_scale": None,
                    "nullable": "Y",
                }
            },
        )
        report = rec.generate_reconciliation_report(_mapping())
        assert "MAPPING RECONCILIATION REPORT" in report
        assert "Status: VALID" in report

    def test_report_invalid_missing_table(self):
        rec = SchemaReconciler(_DummyAdapter())
        rec._table_exists = lambda _t, _o=None: False
        report = rec.generate_reconciliation_report(_mapping())
        assert "Status: INVALID" in report
        assert "ERRORS:" in report


class TestMappingValidatorAggregate:
    def test_validate_all_mappings_counts(self):
        mv = MappingValidator(_DummyAdapter())
        results = {}

        good = _mapping()
        good.mapping_name = "good"
        bad = _mapping()
        bad.mapping_name = "bad"

        def fake_reconcile(mapping):
            return {"valid": mapping.mapping_name == "good"}

        mv.reconciler.reconcile_mapping = fake_reconcile
        out = mv.validate_all_mappings([good, bad])
        assert out["total_mappings"] == 2
        assert out["valid"] == 1
        assert out["invalid"] == 1
        assert set(out["results"].keys()) == {"good", "bad"}
