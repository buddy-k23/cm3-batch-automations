"""Unit tests for ``scripts.e2e_lib.reconciliation_spec``."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path
from typing import Any, Dict

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.reconciliation_spec import (  # noqa: E402
    SCHEMA_VERSION,
    AssertionSpec,
    Cardinality,
    FieldSpec,
    ReconciliationSpec,
    ReconciliationSpecError,
    RecordTypeSpec,
    load_spec,
    load_spec_from_dict,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


def _minimal_spec_dict() -> Dict[str, Any]:
    """Smallest spec that should parse cleanly."""
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "SHAW",
        "file_type": "TRANERT",
        "umbrella_mapping": "config/mappings/SHAW_TRANERT.yaml",
        "bootstrap_dir": "config/e2e/sources/SHAW/sql/tranert/00_bootstrap",
        "load_dir": "config/e2e/sources/SHAW/sql/tranert/10_load",
        "query_dir": "config/e2e/sources/SHAW/sql/tranert/20_query",
        "record_types": {
            "32000": {
                "expected_sql": "expected_32000.sql",
                "cardinality": "one_per_driver_row",
                "key": ["ACCT_NUM"],
                "fields": [
                    {
                        "file_field": "LN-NUM-ERT",
                        "expected_column": "LN_NUM_ERT",
                    }
                ],
            }
        },
    }


def _full_tranert_spec_dict() -> Dict[str, Any]:
    """Realistic spec covering all 6 TRANERT detail record types."""
    spec = _minimal_spec_dict()
    spec["record_types"] = {
        "32000": {
            "expected_sql": "expected_32000.sql",
            "cardinality": "one_per_driver_row",
            "key": ["ACCT_NUM"],
            "fields": [
                {"file_field": "LN-NUM-ERT", "expected_column": "LN_NUM_ERT"},
                {"file_field": "EFF-DAT-ERT", "expected_column": "EFF_DAT_ERT"},
                {"file_field": "TRN-COD-ERT", "expected_column": "TRN_COD_ERT"},
                {"file_field": "LCT-COD-NEW1", "expected_column": "LCT_COD_NEW1"},
            ],
        },
        "32005": {
            "expected_sql": "expected_32005.sql",
            "cardinality": "many_per_driver_row",
            "key": ["ACCT_NUM", "CONTACT_ID"],
            "fields": [
                {"file_field": "LN-NUM-ERT", "expected_column": "LN_NUM_ERT"},
                {"file_field": "CONTACT-ID", "expected_column": "CONTACT_ID"},
                {
                    "file_field": "ECOA-CODE-CUS",
                    "expected_column": "ECOA_CODE_CUS",
                },
            ],
            "ignored_fields": ["CIF-CBR-RPT-IND-CUS"],
        },
        "32010": {
            "expected_sql": "expected_32010.sql",
            "cardinality": "zero_or_one_per_driver_row",
            "predicate": "CHG_OFF_CD = '1'",
            "key": ["ACCT_NUM"],
            "fields": [
                {"file_field": "LN-NUM-ERT", "expected_column": "LN_NUM_ERT"},
                {
                    "file_field": "OGL-NTE-DAT-ORI",
                    "expected_column": "OGL_NTE_DAT_ORI",
                    "predicate": "CHG_OFF_CD = '1'",
                },
                {
                    "file_field": "ST-COD-ORI",
                    "expected_column": "ST_COD_ORI",
                    "regression_only": True,
                },
            ],
            "ignored_fields": ["LN-TYP-ORI", "REP-TYP-ORI"],
        },
        "32025": {
            "expected_sql": "expected_32025.sql",
            "cardinality": "one_per_driver_row",
            "key": ["ACCT_NUM"],
            "fields": [
                {"file_field": "LN-NUM-ERT", "expected_column": "LN_NUM_ERT"},
                {
                    "file_field": "LGL-STA-COD-COD",
                    "expected_column": "LGL_STA_COD_COD",
                },
            ],
        },
        "32040": {
            "expected_sql": "expected_32040.sql",
            "cardinality": "one_per_driver_row",
            "key": ["ACCT_NUM"],
            "fields": [
                {"file_field": "LN-NUM-ERT", "expected_column": "LN_NUM_ERT"},
                {
                    "file_field": "DAT-DLQ-STR-CBRS",
                    "expected_column": "DAT_DLQ_STR_CBRS",
                    "predicate": "M_FIRST_DELQ_DT <= BATCH_DATE",
                },
            ],
        },
        "32075": {
            "expected_sql": "expected_32075.sql",
            "cardinality": "one_per_driver_row",
            "key": ["ACCT_NUM"],
            "fields": [
                {"file_field": "LN-NUM-ERT", "expected_column": "LN_NUM_ERT"},
                {
                    "file_field": "RCF-DUE-REC",
                    "expected_column": "RCF_DUE_REC",
                },
            ],
        },
    }
    spec["assertions"] = [
        {
            "name": "batch_header_count",
            "expr": "header.ITM-CNT-BRT == sum(detail_row_counts)",
        }
    ]
    return spec


# --------------------------------------------------------------------------- #
# Happy-path tests
# --------------------------------------------------------------------------- #


class TestLoadFromDictHappyPath:
    def test_minimal_spec_parses(self) -> None:
        spec = load_spec_from_dict(_minimal_spec_dict(), where="<test>")
        assert isinstance(spec, ReconciliationSpec)
        assert spec.schema_version == SCHEMA_VERSION
        assert spec.source == "SHAW"
        assert spec.file_type == "TRANERT"
        assert set(spec.record_types) == {"32000"}
        rt = spec.record_types["32000"]
        assert rt.cardinality is Cardinality.ONE_PER_DRIVER_ROW
        assert rt.key == ("ACCT_NUM",)
        assert rt.predicate is None
        assert rt.ignored_fields == ()
        assert len(rt.fields) == 1
        assert rt.fields[0].file_field == "LN-NUM-ERT"
        assert rt.fields[0].predicate is None
        assert rt.fields[0].regression_only is False

    def test_full_tranert_spec_parses(self) -> None:
        spec = load_spec_from_dict(_full_tranert_spec_dict(), where="<test>")
        assert set(spec.record_types) == {
            "32000",
            "32005",
            "32010",
            "32025",
            "32040",
            "32075",
        }
        # 32005 composite key
        assert spec.record_types["32005"].key == ("ACCT_NUM", "CONTACT_ID")
        assert (
            spec.record_types["32005"].cardinality
            is Cardinality.MANY_PER_DRIVER_ROW
        )
        # 32010 predicate + cardinality + ignored
        rt10 = spec.record_types["32010"]
        assert rt10.cardinality is Cardinality.ZERO_OR_ONE_PER_DRIVER_ROW
        assert rt10.predicate == "CHG_OFF_CD = '1'"
        assert "LN-TYP-ORI" in rt10.ignored_fields
        # 32010 field-level predicate
        ogl = next(f for f in rt10.fields if f.file_field == "OGL-NTE-DAT-ORI")
        assert ogl.predicate == "CHG_OFF_CD = '1'"
        # 32010 regression_only
        st = next(f for f in rt10.fields if f.file_field == "ST-COD-ORI")
        assert st.regression_only is True
        # Assertions
        assert len(spec.assertions) == 1
        assert spec.assertions[0].name == "batch_header_count"

    def test_record_type_codes_are_strings_not_ints(self) -> None:
        # YAML happily parses "32010" as an int — confirm we coerce to str.
        data = _minimal_spec_dict()
        data["record_types"] = {
            32000: {  # int key on purpose
                "expected_sql": "expected_32000.sql",
                "cardinality": "one_per_driver_row",
                "key": ["ACCT_NUM"],
                "fields": [
                    {"file_field": "X", "expected_column": "X_COL"}
                ],
            }
        }
        spec = load_spec_from_dict(data, where="<test>")
        assert set(spec.record_types) == {"32000"}
        assert spec.record_types["32000"].record_type == "32000"

    def test_assertions_default_empty_when_omitted(self) -> None:
        spec = load_spec_from_dict(_minimal_spec_dict(), where="<test>")
        assert spec.assertions == ()

    def test_value_types_are_frozen(self) -> None:
        spec = load_spec_from_dict(_minimal_spec_dict(), where="<test>")
        with pytest.raises(Exception):  # FrozenInstanceError
            spec.source = "HACKED"  # type: ignore[misc]
        rt = spec.record_types["32000"]
        with pytest.raises(Exception):
            rt.record_type = "X"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Rejection tests (typo defence + shape defects)
# --------------------------------------------------------------------------- #


class TestLoadFromDictRejection:
    def test_top_level_not_a_mapping(self) -> None:
        with pytest.raises(ReconciliationSpecError, match="top-level"):
            load_spec_from_dict([1, 2, 3], where="<test>")  # type: ignore[arg-type]

    def test_unknown_top_level_key(self) -> None:
        data = _minimal_spec_dict()
        data["typo_key"] = "oops"
        with pytest.raises(ReconciliationSpecError, match="typo_key"):
            load_spec_from_dict(data, where="<test>")

    def test_missing_record_types(self) -> None:
        data = _minimal_spec_dict()
        del data["record_types"]
        with pytest.raises(ReconciliationSpecError, match="record_types"):
            load_spec_from_dict(data, where="<test>")

    def test_empty_record_types(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"] = {}
        with pytest.raises(
            ReconciliationSpecError, match="at least one record type"
        ):
            load_spec_from_dict(data, where="<test>")

    def test_wrong_schema_version(self) -> None:
        data = _minimal_spec_dict()
        data["schema_version"] = 2
        with pytest.raises(ReconciliationSpecError, match="schema_version"):
            load_spec_from_dict(data, where="<test>")

    def test_schema_version_not_int(self) -> None:
        data = _minimal_spec_dict()
        data["schema_version"] = "1"  # string, not int
        with pytest.raises(ReconciliationSpecError, match="schema_version"):
            load_spec_from_dict(data, where="<test>")

    def test_empty_source(self) -> None:
        data = _minimal_spec_dict()
        data["source"] = ""
        with pytest.raises(ReconciliationSpecError, match="source"):
            load_spec_from_dict(data, where="<test>")

    def test_unknown_cardinality(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["cardinality"] = "two_per_driver_row"
        with pytest.raises(
            ReconciliationSpecError, match="unknown cardinality"
        ):
            load_spec_from_dict(data, where="<test>")

    def test_cardinality_must_be_string(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["cardinality"] = 1
        with pytest.raises(ReconciliationSpecError, match="must be a string"):
            load_spec_from_dict(data, where="<test>")

    def test_expected_sql_must_end_dot_sql(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["expected_sql"] = "expected_32000.txt"
        with pytest.raises(ReconciliationSpecError, match=r"must end with '\.sql'"):
            load_spec_from_dict(data, where="<test>")

    def test_empty_key_list(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["key"] = []
        with pytest.raises(
            ReconciliationSpecError, match="non-empty list of column names"
        ):
            load_spec_from_dict(data, where="<test>")

    def test_key_contains_non_string(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["key"] = ["ACCT_NUM", 42]
        with pytest.raises(
            ReconciliationSpecError, match=r"key\[1\]"
        ):
            load_spec_from_dict(data, where="<test>")

    def test_empty_fields_list(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["fields"] = []
        with pytest.raises(
            ReconciliationSpecError, match="non-empty list of field specs"
        ):
            load_spec_from_dict(data, where="<test>")

    def test_unknown_field_spec_key(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["fields"][0]["predcate"] = "oops"
        with pytest.raises(ReconciliationSpecError, match="predcate"):
            load_spec_from_dict(data, where="<test>")

    def test_missing_field_spec_required_key(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["fields"][0] = {"file_field": "X"}
        with pytest.raises(
            ReconciliationSpecError, match="expected_column"
        ):
            load_spec_from_dict(data, where="<test>")

    def test_record_type_predicate_must_be_nonempty(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["predicate"] = ""
        with pytest.raises(ReconciliationSpecError, match="predicate"):
            load_spec_from_dict(data, where="<test>")

    def test_field_predicate_must_be_nonempty(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["fields"][0]["predicate"] = "   "
        with pytest.raises(ReconciliationSpecError, match="predicate"):
            load_spec_from_dict(data, where="<test>")

    def test_regression_only_must_be_bool(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["fields"][0]["regression_only"] = "yes"
        with pytest.raises(ReconciliationSpecError, match="regression_only"):
            load_spec_from_dict(data, where="<test>")

    def test_ignored_fields_must_be_list(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"]["ignored_fields"] = "FOO,BAR"
        with pytest.raises(ReconciliationSpecError, match="ignored_fields"):
            load_spec_from_dict(data, where="<test>")

    def test_assertion_missing_name(self) -> None:
        data = _minimal_spec_dict()
        data["assertions"] = [{"expr": "1 == 1"}]
        with pytest.raises(ReconciliationSpecError, match="name"):
            load_spec_from_dict(data, where="<test>")

    def test_assertions_must_be_list(self) -> None:
        data = _minimal_spec_dict()
        data["assertions"] = {"a": "b"}
        with pytest.raises(ReconciliationSpecError, match="assertions"):
            load_spec_from_dict(data, where="<test>")

    def test_record_type_block_not_a_mapping(self) -> None:
        data = _minimal_spec_dict()
        data["record_types"]["32000"] = "not a mapping"
        with pytest.raises(
            ReconciliationSpecError, match="must be a mapping"
        ):
            load_spec_from_dict(data, where="<test>")


# --------------------------------------------------------------------------- #
# load_spec (file-based) tests
# --------------------------------------------------------------------------- #


class TestLoadSpecFromFile:
    def test_file_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yml"
        with pytest.raises(
            ReconciliationSpecError, match="spec file not found"
        ):
            load_spec(missing)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yml"
        bad.write_text("foo: : :\n  - bar\n", encoding="utf-8")
        with pytest.raises(ReconciliationSpecError, match="failed to parse"):
            load_spec(bad)

    def test_top_level_not_mapping(self, tmp_path: Path) -> None:
        f = tmp_path / "list.yml"
        f.write_text("- a\n- b\n", encoding="utf-8")
        with pytest.raises(
            ReconciliationSpecError, match="top-level YAML must be a mapping"
        ):
            load_spec(f)

    def test_round_trip_via_yaml(self, tmp_path: Path) -> None:
        yaml_text = textwrap.dedent(
            """\
            schema_version: 1
            source: SHAW
            file_type: TRANERT
            umbrella_mapping: config/mappings/SHAW_TRANERT.yaml
            bootstrap_dir: config/e2e/sources/SHAW/sql/tranert/00_bootstrap
            load_dir:      config/e2e/sources/SHAW/sql/tranert/10_load
            query_dir:     config/e2e/sources/SHAW/sql/tranert/20_query
            record_types:
              "32000":
                expected_sql: expected_32000.sql
                cardinality: one_per_driver_row
                key: [ACCT_NUM]
                fields:
                  - file_field: LN-NUM-ERT
                    expected_column: LN_NUM_ERT
              "32010":
                expected_sql: expected_32010.sql
                cardinality: zero_or_one_per_driver_row
                predicate: "CHG_OFF_CD = '1'"
                key: [ACCT_NUM]
                fields:
                  - file_field: LN-NUM-ERT
                    expected_column: LN_NUM_ERT
                ignored_fields: [LN-TYP-ORI]
            assertions:
              - name: batch_header_count
                expr: "header.ITM-CNT-BRT == sum(detail_row_counts)"
            """
        )
        f = tmp_path / "spec.yml"
        f.write_text(yaml_text, encoding="utf-8")
        spec = load_spec(f)
        assert spec.source == "SHAW"
        assert set(spec.record_types) == {"32000", "32010"}
        assert (
            spec.record_types["32010"].cardinality
            is Cardinality.ZERO_OR_ONE_PER_DRIVER_ROW
        )
        assert spec.record_types["32010"].predicate == "CHG_OFF_CD = '1'"
        assert spec.record_types["32010"].ignored_fields == ("LN-TYP-ORI",)
        assert len(spec.assertions) == 1


# --------------------------------------------------------------------------- #
# Enum smoke
# --------------------------------------------------------------------------- #


class TestCardinality:
    def test_all_three_values_round_trip(self) -> None:
        for value in (
            "one_per_driver_row",
            "zero_or_one_per_driver_row",
            "many_per_driver_row",
        ):
            assert Cardinality.parse(value, where="<test>").value == value

    def test_string_inheritance(self) -> None:
        # Cardinality is a (str, Enum) — equality with raw string holds.
        assert Cardinality.ONE_PER_DRIVER_ROW == "one_per_driver_row"


# --------------------------------------------------------------------------- #
# Public surface smoke (instantiate the frozen dataclasses directly)
# --------------------------------------------------------------------------- #


class TestDataclasses:
    def test_field_spec_defaults(self) -> None:
        f = FieldSpec(file_field="A", expected_column="A_COL")
        assert f.predicate is None
        assert f.regression_only is False

    def test_record_type_spec_defaults(self) -> None:
        rt = RecordTypeSpec(
            record_type="X",
            expected_sql="x.sql",
            cardinality=Cardinality.ONE_PER_DRIVER_ROW,
            key=("ID",),
            fields=(FieldSpec(file_field="F", expected_column="F_COL"),),
        )
        assert rt.predicate is None
        assert rt.ignored_fields == ()

    def test_assertion_spec_holds_fields(self) -> None:
        a = AssertionSpec(name="x", expr="1 == 1")
        assert a.name == "x"
        assert a.expr == "1 == 1"
