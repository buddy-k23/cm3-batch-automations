"""Unit tests for ``scripts.e2e_lib.db_truth_comparator``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
from unittest.mock import MagicMock

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.db_truth_comparator import (  # noqa: E402
    DbTruthComparatorError,
    PerTypeCount,
    ReconciliationReport,
    ReconciliationViolation,
    reconcile,
)
from scripts.e2e_lib.reconciliation_spec import (  # noqa: E402
    SCHEMA_VERSION,
    load_spec_from_dict,
)

# --------------------------------------------------------------------------- #
# Fixture builders
# --------------------------------------------------------------------------- #


def _write_mapping_json(path: Path, name: str, fields: List[Dict[str, Any]]) -> Path:
    """Per-record-type mapping JSON in the UniversalMappingParser shape."""
    content = {
        "mapping_name": name,
        "version": "1.0.0",
        "source": {"format": "fixed_width", "encoding": "UTF-8"},
        "target": {"type": "database"},
        "fields": fields,
        "key_columns": [],
    }
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _write_umbrella(
    path: Path,
    record_types: Dict[str, Dict[str, Any]],
    *,
    default_action: str = "error",
) -> Path:
    """Umbrella YAML in the MultiRecordConfig shape."""
    body = {
        "discriminator": {"field": "record_type", "position": 1, "length": 3},
        "record_types": record_types,
        "default_action": default_action,
    }
    # Use yaml.safe_dump so Windows backslash paths are emitted without
    # being mis-interpreted as escape sequences by the double-quoted scalar
    # grammar.
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def _build_two_type_fixture(
    tmp_path: Path,
    *,
    default_action: str = "error",
    with_assertion: bool = False,
) -> Tuple[Path, Any, Path]:
    """Build a 2-record-type fixture: HDR (position=first) + DET (match).

    Returns ``(spec_path, spec, file_path)``.

    Layout (all 1-indexed, fixed-width):
      HDR: TYPE (1-3) + COUNT (4-9)             â† position=first
      DET: TYPE (1-3) + ACCT_NUM (4-12) + AMOUNT (13-20)
    """
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    query_dir = spec_dir / "query"
    query_dir.mkdir()
    bootstrap_dir = spec_dir / "bootstrap"
    bootstrap_dir.mkdir()
    load_dir = spec_dir / "load"
    load_dir.mkdir()

    # Empty bootstrap/load directories â€” skip_bootstrap=True for most
    # tests; the BootstrapOrchestration class explicitly exercises this.

    # Per-record-type mapping JSONs.
    hdr_mapping = _write_mapping_json(
        tmp_path / "hdr.json",
        "HDR_mapping",
        [
            {"name": "TYPE", "data_type": "string", "position": 1, "length": 3},
            {"name": "COUNT", "data_type": "string", "position": 4, "length": 6},
        ],
    )
    det_mapping = _write_mapping_json(
        tmp_path / "det.json",
        "DET_mapping",
        [
            {"name": "TYPE", "data_type": "string", "position": 1, "length": 3},
            {"name": "ACCT_NUM", "data_type": "string", "position": 4, "length": 9},
            {"name": "AMOUNT", "data_type": "string", "position": 13, "length": 8},
        ],
    )

    # Umbrella YAML.
    umbrella_path = tmp_path / "umbrella.yaml"
    _write_umbrella(
        umbrella_path,
        record_types={
            "header": {
                "match": "",
                "position": "first",
                "mapping": str(hdr_mapping),
            },
            "detail": {
                "match": "DET",
                "position": "",
                "mapping": str(det_mapping),
            },
        },
        default_action=default_action,
    )

    # Expected SQL files (text only; cursor stub returns canned rows).
    # Each file embeds a distinctive view-name marker so the rowmap
    # cursor matcher can dispatch on substring without ambiguity.
    (query_dir / "expected_header.sql").write_text(
        "SELECT type, count FROM expected_header_view\n", encoding="utf-8"
    )
    (query_dir / "expected_detail.sql").write_text(
        "SELECT acct_num, amount FROM expected_detail_view\n", encoding="utf-8"
    )

    # ReconciliationSpec.
    spec_dict = {
        "schema_version": SCHEMA_VERSION,
        "source": "TEST",
        "file_type": "FX",
        "umbrella_mapping": str(umbrella_path),
        "bootstrap_dir": str(bootstrap_dir),
        "load_dir": str(load_dir),
        "query_dir": str(query_dir),
        "record_types": {
            "header": {
                "expected_sql": "expected_header.sql",
                "cardinality": "one_per_driver_row",
                "key": ["TYPE"],
                "fields": [
                    {"file_field": "COUNT", "expected_column": "COUNT"},
                ],
            },
            "detail": {
                "expected_sql": "expected_detail.sql",
                "cardinality": "one_per_driver_row",
                "key": ["ACCT_NUM"],
                "fields": [
                    {"file_field": "AMOUNT", "expected_column": "AMOUNT"},
                ],
            },
        },
    }
    if with_assertion:
        spec_dict["assertions"] = [
            {
                "name": "header_count_matches_details",
                "expr": "header.COUNT == count(detail)",
            }
        ]
    spec = load_spec_from_dict(spec_dict, where="<test>")

    # Data file. HDR row (9 chars: "HDR" + "000002"); DET rows.
    file_path = tmp_path / "data.txt"
    file_path.write_text(
        "HDR000002\n" "DET100000001000100.5\n" "DET200000002000250.0\n",
        encoding="utf-8",
    )
    return spec, spec, file_path  # spec returned twice for compat with older patterns


def _conn_with_rowsets(
    rowsets: Sequence[Sequence[Tuple[Sequence[str], List[Sequence[Any]]]]],
) -> MagicMock:
    """Build a MagicMock connection where each cursor.execute returns canned rows.

    ``rowsets`` is a list of (columns, rows) pairs, consumed in execute-call
    order. Each call to ``conn.cursor()`` returns a fresh cursor mock so
    we can independently set ``description`` and ``fetchall`` per call.
    """
    cursors: List[MagicMock] = []
    for columns, rows in rowsets:
        cur = MagicMock(name=f"cursor_{len(cursors)}")
        cur.description = [(col,) for col in columns]
        cur.fetchall.return_value = list(rows)
        cursors.append(cur)
    conn = MagicMock(name="conn")
    conn.cursor.side_effect = list(cursors)
    return conn


def _conn_with_rowmap(
    rowmap: Dict[str, Tuple[Sequence[str], List[Sequence[Any]]]],
) -> MagicMock:
    """Like ``_conn_with_rowsets`` but matches by the SQL text passed to execute.

    Useful when the engine queries multiple expected SQL files in spec
    iteration order; matching by SQL substring is more robust than
    matching by call index.
    """

    def _make_cursor() -> MagicMock:
        cur = MagicMock()

        def _execute(sql: str, *_args: Any, **_kwargs: Any) -> None:
            for needle, (columns, rows) in rowmap.items():
                if needle in sql:
                    cur.description = [(col,) for col in columns]
                    cur.fetchall.return_value = list(rows)
                    return
            cur.description = []
            cur.fetchall.return_value = []

        cur.execute.side_effect = _execute
        return cur

    conn = MagicMock(name="conn")
    conn.cursor.side_effect = _make_cursor
    return conn


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


class TestHappyPath:
    """Two record types, matching expected rows â€” empty violations."""

    def test_clean_run_emits_no_violations(self, tmp_path: Path) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path)
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        assert report.violations == ()
        assert report.rows_compared == 3
        assert report.rows_unknown_type == 0
        assert report.per_type_counts["header"] == PerTypeCount(
            file_rows=1, expected_rows=1, field_mismatches=0
        )
        assert report.per_type_counts["detail"] == PerTypeCount(
            file_rows=2, expected_rows=2, field_mismatches=0
        )
        assert report.spec_source == "TEST"
        assert report.spec_file_type == "FX"


# --------------------------------------------------------------------------- #
# Field-level mismatch
# --------------------------------------------------------------------------- #


class TestFieldMismatch:
    """One field differs on one row â€” exactly one ``field_mismatch``."""

    def test_single_field_mismatch_surfaces_with_full_context(
        self, tmp_path: Path
    ) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path)
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    # AMOUNT for the first detail row deliberately differs.
                    [("100000001", "999999.9"), ("200000002", "000250.0")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        mismatches = [v for v in report.violations if v.kind == "field_mismatch"]
        assert len(mismatches) == 1
        v = mismatches[0]
        assert v.record_type == "detail"
        assert v.field == "AMOUNT"
        assert v.expected == "999999.9"
        assert v.actual == "000100.5"
        assert v.key_values == ("100000001",)
        assert v.line_number == 2
        assert report.per_type_counts["detail"].field_mismatches == 1


# --------------------------------------------------------------------------- #
# Missing / unexpected rows
# --------------------------------------------------------------------------- #


class TestMissingAndUnexpectedRows:
    """File-only or SQL-only keys surface the right kinds."""

    def test_missing_expected_when_sql_has_key_file_does_not(
        self, tmp_path: Path
    ) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path)
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [
                        ("100000001", "000100.5"),
                        ("200000002", "000250.0"),
                        ("300000003", "000999.0"),  # extra expected row
                    ],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        missing = [v for v in report.violations if v.kind == "missing_expected"]
        assert len(missing) == 1
        assert missing[0].record_type == "detail"
        assert missing[0].key_values == ("300000003",)
        assert missing[0].line_number is None

    def test_unexpected_file_row_when_file_has_key_sql_does_not(
        self, tmp_path: Path
    ) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path)
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5")],  # SQL is missing 200000002
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        unexpected = [v for v in report.violations if v.kind == "unexpected_file_row"]
        assert len(unexpected) == 1
        assert unexpected[0].record_type == "detail"
        assert unexpected[0].key_values == ("200000002",)
        assert unexpected[0].line_number == 3


# --------------------------------------------------------------------------- #
# Cardinality
# --------------------------------------------------------------------------- #


class TestCardinality:
    """The three cardinality kinds emit the right violation shapes."""

    def test_one_per_driver_row_with_duplicate_file_row_violates(
        self, tmp_path: Path
    ) -> None:
        _, spec, _ = _build_two_type_fixture(tmp_path)
        # Override the data file with a duplicated detail row.
        file_path = next(iter(spec.record_types)).__class__  # noqa â€” placeholder
        file_path = tmp_path / "data_dup.txt"
        file_path.write_text(
            "HDR000003\n"
            "DET100000001000100.5\n"
            "DET100000001000100.5\n"
            "DET200000002000250.0\n",
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000003")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        card_violations = [
            v for v in report.violations if v.kind == "cardinality_violation"
        ]
        assert len(card_violations) == 1
        assert card_violations[0].record_type == "detail"
        assert card_violations[0].key_values == ("100000001",)
        assert "exactly 1" in card_violations[0].expected

    def test_zero_or_one_with_two_file_rows_violates(self, tmp_path: Path) -> None:
        # Build a fixture with detail.cardinality=zero_or_one.
        _, spec, _ = _build_two_type_fixture(tmp_path)
        spec_dict = {
            "schema_version": SCHEMA_VERSION,
            "source": "TEST",
            "file_type": "FX",
            "umbrella_mapping": spec.umbrella_mapping,
            "bootstrap_dir": spec.bootstrap_dir,
            "load_dir": spec.load_dir,
            "query_dir": spec.query_dir,
            "record_types": {
                "header": {
                    "expected_sql": "expected_header.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["TYPE"],
                    "fields": [{"file_field": "COUNT", "expected_column": "COUNT"}],
                },
                "detail": {
                    "expected_sql": "expected_detail.sql",
                    "cardinality": "zero_or_one_per_driver_row",
                    "key": ["ACCT_NUM"],
                    "fields": [{"file_field": "AMOUNT", "expected_column": "AMOUNT"}],
                },
            },
        }
        spec = load_spec_from_dict(spec_dict, where="<test-zero-or-one>")

        file_path = tmp_path / "data_zo.txt"
        file_path.write_text(
            "HDR000002\n"
            "DET100000001000100.5\n"
            "DET100000001000100.6\n",  # two rows at same key
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        card_violations = [
            v for v in report.violations if v.kind == "cardinality_violation"
        ]
        assert len(card_violations) == 1
        assert "0 or 1" in card_violations[0].expected

    def test_many_per_driver_row_with_matched_multiset_is_clean(
        self, tmp_path: Path
    ) -> None:
        _, base_spec, _ = _build_two_type_fixture(tmp_path)
        spec_dict = {
            "schema_version": SCHEMA_VERSION,
            "source": "TEST",
            "file_type": "FX",
            "umbrella_mapping": base_spec.umbrella_mapping,
            "bootstrap_dir": base_spec.bootstrap_dir,
            "load_dir": base_spec.load_dir,
            "query_dir": base_spec.query_dir,
            "record_types": {
                "header": {
                    "expected_sql": "expected_header.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["TYPE"],
                    "fields": [{"file_field": "COUNT", "expected_column": "COUNT"}],
                },
                "detail": {
                    "expected_sql": "expected_detail.sql",
                    "cardinality": "many_per_driver_row",
                    "key": ["ACCT_NUM"],
                    "fields": [{"file_field": "AMOUNT", "expected_column": "AMOUNT"}],
                },
            },
        }
        spec = load_spec_from_dict(spec_dict, where="<test-many>")

        file_path = tmp_path / "data_many.txt"
        file_path.write_text(
            "HDR000003\n"
            "DET100000001000100.5\n"
            "DET100000001000100.6\n"
            "DET200000002000250.0\n",
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000003")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [
                        ("100000001", "000100.5"),
                        ("100000001", "000100.6"),
                        ("200000002", "000250.0"),
                    ],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        assert report.violations == ()


# --------------------------------------------------------------------------- #
# Regression-only and ignored fields
# --------------------------------------------------------------------------- #


class TestRegressionOnlyAndIgnored:
    """Fields flagged regression_only are skipped; ignored_fields surface nowhere."""

    def _build_spec_with_flags(
        self, tmp_path: Path, *, regression_only: bool, ignored: bool
    ) -> Tuple[Any, Path]:
        _, base_spec, file_path = _build_two_type_fixture(tmp_path)
        detail_fields: List[Dict[str, Any]] = [
            {
                "file_field": "AMOUNT",
                "expected_column": "AMOUNT",
                "regression_only": regression_only,
            }
        ]
        spec_dict = {
            "schema_version": SCHEMA_VERSION,
            "source": "TEST",
            "file_type": "FX",
            "umbrella_mapping": base_spec.umbrella_mapping,
            "bootstrap_dir": base_spec.bootstrap_dir,
            "load_dir": base_spec.load_dir,
            "query_dir": base_spec.query_dir,
            "record_types": {
                "header": {
                    "expected_sql": "expected_header.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["TYPE"],
                    "fields": [{"file_field": "COUNT", "expected_column": "COUNT"}],
                },
                "detail": {
                    "expected_sql": "expected_detail.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["ACCT_NUM"],
                    "fields": detail_fields,
                    "ignored_fields": ["ACCT_NUM"] if ignored else [],
                },
            },
        }
        return load_spec_from_dict(spec_dict, where="<flags>"), file_path

    def test_regression_only_field_difference_is_not_a_violation(
        self, tmp_path: Path
    ) -> None:
        spec, file_path = self._build_spec_with_flags(
            tmp_path, regression_only=True, ignored=False
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "999999.9"), ("200000002", "999999.9")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        assert report.violations == ()
        # Row still counted.
        assert report.per_type_counts["detail"].file_rows == 2
        assert report.per_type_counts["detail"].field_mismatches == 0

    def test_ignored_fields_do_not_produce_violations(self, tmp_path: Path) -> None:
        # AMOUNT is reconciled normally; ACCT_NUM is in ignored_fields but
        # it's also the key. The engine compares fields, not keys â€” the
        # key is used for joining, and ignored_fields is only relevant if
        # the field is in ``fields``. Confirm a clean run when only the
        # reconciled field matches.
        spec, file_path = self._build_spec_with_flags(
            tmp_path, regression_only=False, ignored=True
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        assert report.violations == ()


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #


class TestAssertions:
    """Assertion grammar parsing and evaluation."""

    def test_header_count_matches_detail_count_is_clean(self, tmp_path: Path) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path, with_assertion=True)
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        assert all(v.kind != "assertion_failed" for v in report.violations)

    def test_header_count_mismatch_emits_assertion_failed(self, tmp_path: Path) -> None:
        # File header says COUNT=000003 but file has only 2 detail rows.
        _, spec, _ = _build_two_type_fixture(tmp_path, with_assertion=True)
        file_path = tmp_path / "data_wrong_count.txt"
        file_path.write_text(
            "HDR000003\n" "DET100000001000100.5\n" "DET200000002000250.0\n",
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000003")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        assertion_failures = [
            v for v in report.violations if v.kind == "assertion_failed"
        ]
        assert len(assertion_failures) == 1
        assert "header_count_matches_details" in assertion_failures[0].message
        assert assertion_failures[0].record_type is None
        assert assertion_failures[0].key_values == ()
        assert assertion_failures[0].line_number is None

    def test_unsupported_operator_raises_at_call_time(self, tmp_path: Path) -> None:
        _, base_spec, file_path = _build_two_type_fixture(tmp_path)
        spec_dict = {
            "schema_version": SCHEMA_VERSION,
            "source": "TEST",
            "file_type": "FX",
            "umbrella_mapping": base_spec.umbrella_mapping,
            "bootstrap_dir": base_spec.bootstrap_dir,
            "load_dir": base_spec.load_dir,
            "query_dir": base_spec.query_dir,
            "record_types": {
                "header": {
                    "expected_sql": "expected_header.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["TYPE"],
                    "fields": [{"file_field": "COUNT", "expected_column": "COUNT"}],
                },
                "detail": {
                    "expected_sql": "expected_detail.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["ACCT_NUM"],
                    "fields": [{"file_field": "AMOUNT", "expected_column": "AMOUNT"}],
                },
            },
            "assertions": [{"name": "bad_op", "expr": "header.COUNT != 2"}],
        }
        spec = load_spec_from_dict(spec_dict, where="<bad-op>")
        with pytest.raises(DbTruthComparatorError, match=r"!=.*not supported"):
            reconcile(MagicMock(), spec, file_path, skip_bootstrap=True)

    def test_unparseable_side_raises_at_call_time(self, tmp_path: Path) -> None:
        _, base_spec, file_path = _build_two_type_fixture(tmp_path)
        spec_dict = {
            "schema_version": SCHEMA_VERSION,
            "source": "TEST",
            "file_type": "FX",
            "umbrella_mapping": base_spec.umbrella_mapping,
            "bootstrap_dir": base_spec.bootstrap_dir,
            "load_dir": base_spec.load_dir,
            "query_dir": base_spec.query_dir,
            "record_types": {
                "header": {
                    "expected_sql": "expected_header.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["TYPE"],
                    "fields": [{"file_field": "COUNT", "expected_column": "COUNT"}],
                },
                "detail": {
                    "expected_sql": "expected_detail.sql",
                    "cardinality": "one_per_driver_row",
                    "key": ["ACCT_NUM"],
                    "fields": [{"file_field": "AMOUNT", "expected_column": "AMOUNT"}],
                },
            },
            "assertions": [
                # `len(file)` is outside the grammar.
                {"name": "bad_side", "expr": "len(file) == 2"}
            ],
        }
        spec = load_spec_from_dict(spec_dict, where="<bad-side>")
        with pytest.raises(
            DbTruthComparatorError, match=r"does not match the supported grammar"
        ):
            reconcile(MagicMock(), spec, file_path, skip_bootstrap=True)

    def test_non_integer_header_field_raises(self, tmp_path: Path) -> None:
        _, spec, _ = _build_two_type_fixture(tmp_path, with_assertion=True)
        # Header line uses non-numeric COUNT.
        file_path = tmp_path / "data_nonint.txt"
        file_path.write_text(
            "HDRABCDEF\n" "DET100000001000100.5\n" "DET200000002000250.0\n",
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "ABCDEF")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        with pytest.raises(DbTruthComparatorError, match=r"not an integer"):
            reconcile(conn, spec, file_path, skip_bootstrap=True)


# --------------------------------------------------------------------------- #
# Bootstrap orchestration
# --------------------------------------------------------------------------- #


class TestBootstrapOrchestration:
    """``reconcile`` runs bootstrap then load before any expected SQL."""

    def test_skip_bootstrap_true_suppresses_bootstrap_and_load(
        self, tmp_path: Path
    ) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path)
        # Add a bootstrap statement that would obviously fail if executed.
        (Path(spec.bootstrap_dir) / "00_create.sql").write_text(
            "OBVIOUSLY BROKEN SQL;\n", encoding="utf-8"
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        # Should not raise â€” the broken bootstrap SQL is never reached.
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        assert report.violations == ()

    def test_bootstrap_dir_is_run_before_expected_sql(self, tmp_path: Path) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path)
        # Empty bootstrap and load dirs are valid no-ops.
        (Path(spec.bootstrap_dir) / "00_create.sql").write_text(
            "CREATE OR REPLACE VIEW v_bootstrap AS SELECT 1 FROM dual;\n",
            encoding="utf-8",
        )
        (Path(spec.load_dir) / "10_load.sql").write_text(
            "MERGE INTO t USING dual ON (1=1) WHEN MATCHED THEN UPDATE SET x = 1;\n",
            encoding="utf-8",
        )

        # Build a connection whose cursor records every execute() in order.
        executed_sql: List[str] = []

        def _make_cursor() -> MagicMock:
            cur = MagicMock()

            def _execute(sql: str, *_args: Any, **_kwargs: Any) -> None:
                executed_sql.append(sql)
                cur.description = []
                cur.fetchall.return_value = []
                # Provide expected rowsets for the two expected SQLs by
                # matching on their distinctive views.
                if "expected_header" in sql or "expected_header.sql" in sql:
                    cur.description = [("TYPE",), ("COUNT",)]
                    cur.fetchall.return_value = [("HDR", "000002")]
                elif "expected_detail_view" in sql:
                    cur.description = [("ACCT_NUM",), ("AMOUNT",)]
                    cur.fetchall.return_value = [
                        ("100000001", "000100.5"),
                        ("200000002", "000250.0"),
                    ]

            cur.execute.side_effect = _execute
            return cur

        conn = MagicMock()
        conn.cursor.side_effect = _make_cursor
        reconcile(conn, spec, file_path, skip_bootstrap=False)

        # The first executed SQL must be the bootstrap view; the second
        # must be the load merge; expected SQLs come after.
        assert any("v_bootstrap" in s for s in executed_sql[:1]), executed_sql
        assert any("MERGE INTO t" in s for s in executed_sql[1:2]), executed_sql
        assert any("expected_header" in s for s in executed_sql[2:]), executed_sql


# --------------------------------------------------------------------------- #
# Unknown record types
# --------------------------------------------------------------------------- #


class TestUnknownRecordType:
    """Behaviour under each umbrella ``default_action``."""

    def test_warn_default_action_emits_unknown_record_type_violations(
        self, tmp_path: Path
    ) -> None:
        _, spec, _ = _build_two_type_fixture(tmp_path, default_action="warn")
        file_path = tmp_path / "data_with_unknown.txt"
        file_path.write_text(
            "HDR000002\n"
            "DET100000001000100.5\n"
            "XXXunknownrowcontent\n"
            "DET200000002000250.0\n",
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        unknowns = [v for v in report.violations if v.kind == "unknown_record_type"]
        assert len(unknowns) == 1
        assert unknowns[0].line_number == 3
        assert report.rows_unknown_type == 1

    def test_error_default_action_translates_parser_raise_to_engine_error(
        self, tmp_path: Path
    ) -> None:
        _, spec, _ = _build_two_type_fixture(tmp_path, default_action="error")
        file_path = tmp_path / "data_with_unknown.txt"
        file_path.write_text(
            "HDR000002\n" "DET100000001000100.5\n" "XXXunknownrowcontent\n",
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5")],
                ),
            }
        )
        with pytest.raises(DbTruthComparatorError, match=r"file parsing failed"):
            reconcile(conn, spec, file_path, skip_bootstrap=True)


# --------------------------------------------------------------------------- #
# Violation ordering
# --------------------------------------------------------------------------- #


class TestViolationOrdering:
    """Mixed violation kinds emerge in a stable sort order."""

    def test_per_record_type_violations_precede_assertion_failures(
        self, tmp_path: Path
    ) -> None:
        _, spec, _ = _build_two_type_fixture(tmp_path, with_assertion=True)
        file_path = tmp_path / "mixed.txt"
        # Header reports 5 but only 2 detail rows present â†’ assertion fail.
        # AMOUNT mismatch on first detail row â†’ field_mismatch.
        # ACCT_NUM 300 in SQL absent from file â†’ missing_expected.
        file_path.write_text(
            "HDR000005\n" "DET100000001000100.5\n" "DET200000002000250.0\n",
            encoding="utf-8",
        )
        conn = _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000005")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [
                        ("100000001", "999999.9"),  # field mismatch
                        ("200000002", "000250.0"),
                        ("300000003", "000300.0"),  # missing_expected
                    ],
                ),
            }
        )
        report = reconcile(conn, spec, file_path, skip_bootstrap=True)
        kinds = [v.kind for v in report.violations]
        # Per-record-type kinds (record_type not None) precede assertion
        # failures (record_type None).
        last_typed_idx = max(
            i for i, v in enumerate(report.violations) if v.record_type is not None
        )
        first_untyped_idx = min(
            i for i, v in enumerate(report.violations) if v.record_type is None
        )
        assert last_typed_idx < first_untyped_idx, kinds


# --------------------------------------------------------------------------- #
# Report value types are frozen
# --------------------------------------------------------------------------- #


class TestReportTypesAreFrozen:
    """All public dataclasses reject mutation."""

    def test_per_type_count_is_frozen(self) -> None:
        c = PerTypeCount(file_rows=1, expected_rows=1, field_mismatches=0)
        with pytest.raises((AttributeError, TypeError)):
            c.file_rows = 99  # type: ignore[misc]

    def test_violation_is_frozen(self) -> None:
        v = ReconciliationViolation(
            kind="field_mismatch",
            record_type="rt",
            key_values=("k",),
            field="f",
            expected="e",
            actual="a",
            message="m",
            line_number=1,
        )
        with pytest.raises((AttributeError, TypeError)):
            v.kind = "other"  # type: ignore[misc]

    def test_report_is_frozen(self) -> None:
        r = ReconciliationReport(
            spec_source="s",
            spec_file_type="f",
            rows_compared=0,
            rows_unknown_type=0,
            violations=(),
            per_type_counts={},
        )
        with pytest.raises((AttributeError, TypeError)):
            r.rows_compared = 99  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Configuration defects
# --------------------------------------------------------------------------- #


class TestConfigurationDefects:
    """Missing files / unloadable inputs raise ``DbTruthComparatorError``."""

    def test_missing_expected_sql_file_raises(self, tmp_path: Path) -> None:
        _, spec, file_path = _build_two_type_fixture(tmp_path)
        # Delete one expected SQL file.
        (Path(spec.query_dir) / "expected_detail.sql").unlink()
        with pytest.raises(
            DbTruthComparatorError, match=r"expected SQL file not found.*detail"
        ):
            reconcile(MagicMock(), spec, file_path, skip_bootstrap=True)

    def test_missing_umbrella_mapping_raises(self, tmp_path: Path) -> None:
        _, base_spec, file_path = _build_two_type_fixture(tmp_path)
        Path(base_spec.umbrella_mapping).unlink()
        with pytest.raises(DbTruthComparatorError, match=r"umbrella mapping not found"):
            reconcile(MagicMock(), base_spec, file_path, skip_bootstrap=True)


# --------------------------------------------------------------------------- #
# R-01c: optional SqlDialect seam on reconcile()
# --------------------------------------------------------------------------- #


class TestDialectSeam:
    """reconcile() accepts an optional SqlDialect (ADR 0010 / R-01c).

    The seam must be behaviour-neutral for the current Oracle path: passing the
    Oracle dialect (or omitting it) yields an identical report.
    """

    def _clean_conn(self):
        return _conn_with_rowmap(
            {
                "expected_header_view": (["TYPE", "COUNT"], [("HDR", "000002")]),
                "expected_detail_view": (
                    ["ACCT_NUM", "AMOUNT"],
                    [("100000001", "000100.5"), ("200000002", "000250.0")],
                ),
            }
        )

    def test_default_dialect_matches_explicit_oracle_dialect(self, tmp_path):
        from src.database.truth_source import ORACLE_DIALECT

        _, spec, file_path = _build_two_type_fixture(tmp_path)

        report_default = reconcile(
            self._clean_conn(), spec, file_path, skip_bootstrap=True
        )
        report_oracle = reconcile(
            self._clean_conn(),
            spec,
            file_path,
            skip_bootstrap=True,
            dialect=ORACLE_DIALECT,
        )

        # Behaviour-neutral: same verdict, same counts.
        assert report_default.violations == report_oracle.violations == ()
        assert report_default.rows_compared == report_oracle.rows_compared == 3
        assert (
            report_default.per_type_counts["detail"]
            == report_oracle.per_type_counts["detail"]
        )

    def test_custom_dialect_is_accepted_without_changing_oracle_result(self, tmp_path):
        # A non-Oracle dialect HINT does not (yet) change engine behaviour for
        # Oracle-shaped expected SQL — the seam exists; branching is future work.
        from src.database.truth_source import SqlDialect

        _, spec, file_path = _build_two_type_fixture(tmp_path)
        report = reconcile(
            self._clean_conn(),
            spec,
            file_path,
            skip_bootstrap=True,
            dialect=SqlDialect(name="postgres"),
        )
        assert report.violations == ()
        assert report.rows_compared == 3

    @pytest.mark.skip(
        reason="R-01c seam only; a real non-Oracle TruthSource adapter + "
        "backend-dialect expected_*.sql is future work (no consumer yet)."
    )
    def test_second_backend_extension_point(self):
        # Documents the intended extension shape for probe P3:
        #   1. Implement a TruthSource adapter (own connect() + SqlDialect).
        #   2. Ship a backend-dialect query_dir with expected_*.sql.
        #   3. reconcile(conn, spec, file_path, dialect=<backend dialect>).
        # When a concrete backend lands, replace this skip with a real fixture.
        raise AssertionError("placeholder — see ADR 0010 R-01c section")
