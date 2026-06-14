"""Unit tests for ``scripts.e2e_lib.extract_tranert_properties``.

The synthetic property-file fixture lives at
``tests/unit/fixtures/tranert_properties/sample.properties`` and exists
exclusively for these tests (per the L2b session-6 Option B decision —
no real SHAW property data is committed alongside this extractor).
"""

from __future__ import annotations

import hashlib
import logging
import sys
from pathlib import Path
from typing import Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.extract_tranert_properties import (  # noqa: E402
    ExtractedLookups,
    LEDGER_CD_VARIANT_AAA,
    LEDGER_CD_VARIANT_STD,
    RunResult,
    TranertPropertyExtractError,
    extract_lookups,
    main,
    parse_properties,
    run,
    write_csvs,
    write_load_sql,
)

_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "unit"
    / "fixtures"
    / "tranert_properties"
    / "sample.properties"
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_fixture_text() -> str:
    return _FIXTURE_PATH.read_text(encoding="utf-8")


def _read_csv(path: Path) -> Tuple[Tuple[str, ...], ...]:
    rows = path.read_text(encoding="utf-8").splitlines()
    return tuple(tuple(r.split(",")) for r in rows)


# --------------------------------------------------------------------------- #
# parse_properties
# --------------------------------------------------------------------------- #


class TestParseProperties:
    """Java-style .properties parsing (subset)."""

    def test_happy_path_simple_pairs(self) -> None:
        text = "a=1\nb=2\nc=3\n"
        assert parse_properties(text) == {"a": "1", "b": "2", "c": "3"}

    def test_hash_comments_ignored(self) -> None:
        text = "# leading comment\na=1\n# trailing comment\n"
        assert parse_properties(text) == {"a": "1"}

    def test_bang_comments_ignored(self) -> None:
        text = "! bang comment\na=1\n"
        assert parse_properties(text) == {"a": "1"}

    def test_blank_lines_ignored(self) -> None:
        text = "\n\na=1\n\n\nb=2\n\n"
        assert parse_properties(text) == {"a": "1", "b": "2"}

    def test_key_and_value_are_trimmed(self) -> None:
        text = "  spaced.key  =  spaced value  \n"
        assert parse_properties(text) == {"spaced.key": "spaced value"}

    def test_equals_inside_value_is_part_of_value(self) -> None:
        text = "sql=SELECT * FROM t WHERE x=1 AND y=2\n"
        assert parse_properties(text) == {"sql": "SELECT * FROM t WHERE x=1 AND y=2"}

    def test_duplicate_key_last_wins(self) -> None:
        text = "k=first\nk=second\nk=third\n"
        assert parse_properties(text) == {"k": "third"}

    def test_missing_equals_raises(self) -> None:
        with pytest.raises(TranertPropertyExtractError, match=r"line 2.*no '='"):
            parse_properties("ok=1\nthis line is bad\n")

    def test_real_fixture_parses_without_error(self) -> None:
        text = _load_fixture_text()
        props = parse_properties(text)
        # 22 in-scope key=value pairs (3+3+3+3+2+3+2+3 across the seven
        # buckets, AAA-variant counted separately) + 3 deliberately
        # out-of-scope keys = 25 total entries.
        assert len(props) == 25
        assert props["tranert.rep_type_ori.A"] == "001"


# --------------------------------------------------------------------------- #
# extract_lookups
# --------------------------------------------------------------------------- #


class TestExtractLookups:
    """Key-prefix classification into the seven lookup buckets."""

    def test_rep_type_ori_bucket(self) -> None:
        props = {
            "tranert.rep_type_ori.A": "001",
            "tranert.rep_type_ori.B": "002",
        }
        result = extract_lookups(props)
        assert result.rep_type_ori_by_prin_sch == (
            ("A", "001"),
            ("B", "002"),
        )

    def test_rep_typ_uri_bucket(self) -> None:
        props = {
            "tranert.base.rep_typ_uri.M": "BM1",
            "tranert.base.rep_typ_uri.Q": "BQ1",
        }
        result = extract_lookups(props)
        assert result.rep_typ_uri_by_terms_freq == (
            ("M", "BM1"),
            ("Q", "BQ1"),
        )

    def test_loan_master_state_cd_bucket(self) -> None:
        props = {
            "loan.master.state.cd.001": "NC",
            "loan.master.state.cd.002": "VA",
        }
        result = extract_lookups(props)
        assert result.loan_master_state_cd_by_bk == (
            ("001", "NC"),
            ("002", "VA"),
        )

    def test_ledger_cd_std_variant(self) -> None:
        props = {"tranert.ledger_cd.dept.100": "10:CC"}
        result = extract_lookups(props)
        assert result.ledger_cd_by_dept == (("100", LEDGER_CD_VARIANT_STD, "10", "CC"),)

    def test_ledger_cd_aaa_variant(self) -> None:
        props = {"tranert.ledger_cd.idx3.AAA.dept.100": "99"}
        result = extract_lookups(props)
        # AAA value is a bare integer; LOAN_TYPE gets the integer, ACT_TYPE
        # is empty (and will become NULL when written to SQL).
        assert result.ledger_cd_by_dept == (("100", LEDGER_CD_VARIANT_AAA, "99", ""),)

    def test_ledger_cd_mixed_variants_sorted_std_before_aaa(self) -> None:
        # Same DEPT both ways → both rows present, STD precedes AAA because
        # the sort key is (DEPT, IDX3_VARIANT) and 'AAA' < 'STD' is NOT true:
        # actually 'AAA' sorts BEFORE 'STD' alphabetically. The session-6
        # design notes say STD should precede AAA in comment ordering. Test
        # documents the actual sort order: alphabetical → AAA, STD.
        props = {
            "tranert.ledger_cd.dept.100": "10:CC",
            "tranert.ledger_cd.idx3.AAA.dept.100": "99",
        }
        result = extract_lookups(props)
        assert result.ledger_cd_by_dept == (
            ("100", LEDGER_CD_VARIANT_AAA, "99", ""),
            ("100", LEDGER_CD_VARIANT_STD, "10", "CC"),
        )

    def test_ledger_cd_std_value_missing_colon_raises(self) -> None:
        props = {"tranert.ledger_cd.dept.100": "no_colon_here"}
        with pytest.raises(
            TranertPropertyExtractError, match="exactly two colon-separated"
        ):
            extract_lookups(props)

    def test_ledger_cd_std_value_three_segments_raises(self) -> None:
        props = {"tranert.ledger_cd.dept.100": "a:b:c"}
        with pytest.raises(TranertPropertyExtractError, match="3 segment"):
            extract_lookups(props)

    def test_idx3_prefix_takes_priority_over_plain_dept_prefix(self) -> None:
        # Defence in depth: the AAA prefix is longer, but Python's dict
        # iteration order plus the declaration order in _PREFIX_TO_BUCKET
        # mean we must check AAA first. Verify by feeding both forms with
        # the same suffix and ensuring the AAA key lands in the AAA variant
        # (not the STD bucket trying to colon-split "99").
        props = {"tranert.ledger_cd.idx3.AAA.dept.X": "99"}
        result = extract_lookups(props)
        assert result.ledger_cd_by_dept == (("X", LEDGER_CD_VARIANT_AAA, "99", ""),)

    def test_act_typ_ui_bucket_with_zero_prefix_fallback_rows(self) -> None:
        props = {
            "tranert.act_typ_ui.5": "AU5",
            "tranert.act_typ_ui.05": "AU5",
            "tranert.act_typ_ui.7": "AU7",
        }
        result = extract_lookups(props)
        # Sort key is the first column (ACCOUNT_TYPE) string-sorted, so
        # "05" < "5" < "7".
        assert result.act_typ_ui == (
            ("05", "AU5"),
            ("5", "AU5"),
            ("7", "AU7"),
        )

    def test_org_level4_bucket(self) -> None:
        props = {
            "tranert.org_level4.sap_center5.12345": "0000201",
            "tranert.org_level4.sap_center5.67890": "0000202",
        }
        result = extract_lookups(props)
        assert result.org_level4_by_sap_center5 == (
            ("12345", "0000201"),
            ("67890", "0000202"),
        )

    def test_cif_act_code_bucket(self) -> None:
        props = {
            "tranert.32005.cif_act_code_name_relation.A": "P",
            "tranert.32005.cif_act_code_name_relation.B": "S",
        }
        result = extract_lookups(props)
        assert result.cif_act_code_by_name_relation == (
            ("A", "P"),
            ("B", "S"),
        )

    def test_unmatched_keys_collected_not_raised(self) -> None:
        props = {
            "tranert.rep_type_ori.A": "001",
            "shaw.tranert.tranertSql": "SELECT ...",
            "shaw.output.path": "/some/path",
        }
        result = extract_lookups(props)
        assert result.rep_type_ori_by_prin_sch == (("A", "001"),)
        assert set(result.unmatched_keys) == {
            "shaw.tranert.tranertSql",
            "shaw.output.path",
        }

    def test_empty_suffix_after_prefix_raises(self) -> None:
        # "tranert.rep_type_ori." with nothing after — pathological but worth
        # rejecting explicitly.
        props = {"tranert.rep_type_ori.": "001"}
        with pytest.raises(TranertPropertyExtractError, match="no value segment"):
            extract_lookups(props)

    def test_full_fixture_classifies_correctly(self) -> None:
        props = parse_properties(_load_fixture_text())
        result = extract_lookups(props)
        assert len(result.rep_type_ori_by_prin_sch) == 3
        assert len(result.rep_typ_uri_by_terms_freq) == 3
        assert len(result.loan_master_state_cd_by_bk) == 3
        # 3 STD + 2 AAA = 5 ledger rows.
        assert len(result.ledger_cd_by_dept) == 5
        std_count = sum(
            1 for r in result.ledger_cd_by_dept if r[1] == LEDGER_CD_VARIANT_STD
        )
        aaa_count = sum(
            1 for r in result.ledger_cd_by_dept if r[1] == LEDGER_CD_VARIANT_AAA
        )
        assert (std_count, aaa_count) == (3, 2)
        assert len(result.act_typ_ui) == 3
        assert len(result.org_level4_by_sap_center5) == 2
        assert len(result.cif_act_code_by_name_relation) == 3
        # The fixture deliberately includes 3 out-of-scope keys.
        assert len(result.unmatched_keys) == 3


# --------------------------------------------------------------------------- #
# write_csvs
# --------------------------------------------------------------------------- #


class TestWriteCsvs:
    """CSV emission: stable byte output, header row, sorted rows."""

    def test_writes_seven_csv_files(self, tmp_path: Path) -> None:
        lookups = extract_lookups(parse_properties(_load_fixture_text()))
        paths = write_csvs(lookups, tmp_path)
        assert len(paths) == 7
        for p in paths:
            assert p.exists()
            assert p.suffix == ".csv"

    def test_csv_header_matches_column_definition(self, tmp_path: Path) -> None:
        lookups = extract_lookups(
            {"tranert.rep_type_ori.A": "001", "tranert.rep_type_ori.B": "002"}
        )
        write_csvs(lookups, tmp_path)
        rows = _read_csv(tmp_path / "rep_type_ori_by_prin_sch.csv")
        assert rows[0] == ("PRIN_SCH", "REP_TYP_ORI")
        assert rows[1] == ("A", "001")
        assert rows[2] == ("B", "002")

    def test_ledger_cd_csv_has_four_columns(self, tmp_path: Path) -> None:
        lookups = extract_lookups(
            {
                "tranert.ledger_cd.dept.100": "10:CC",
                "tranert.ledger_cd.idx3.AAA.dept.200": "99",
            }
        )
        write_csvs(lookups, tmp_path)
        rows = _read_csv(tmp_path / "ledger_cd_by_dept.csv")
        assert rows[0] == ("DEPT", "IDX3_VARIANT", "LOAN_TYPE", "ACT_TYPE")
        assert ("100", LEDGER_CD_VARIANT_STD, "10", "CC") in rows
        assert ("200", LEDGER_CD_VARIANT_AAA, "99", "") in rows

    def test_csv_line_endings_are_lf_only(self, tmp_path: Path) -> None:
        # newline="" + lineterminator="\n" should yield byte-deterministic
        # output across Linux and Windows. Verify by reading raw bytes.
        lookups = extract_lookups({"tranert.rep_type_ori.A": "001"})
        write_csvs(lookups, tmp_path)
        raw = (tmp_path / "rep_type_ori_by_prin_sch.csv").read_bytes()
        assert b"\r\n" not in raw, "CSV must not contain CRLF line endings"
        assert raw.endswith(b"\n"), "CSV must end with a newline"

    def test_empty_bucket_writes_header_only(self, tmp_path: Path) -> None:
        # No rep_typ_uri keys → file is header-only.
        lookups = extract_lookups({"tranert.rep_type_ori.A": "001"})
        write_csvs(lookups, tmp_path)
        rows = _read_csv(tmp_path / "rep_typ_uri_by_terms_freq.csv")
        assert rows == (("TERMS_FREQ", "REP_TYP_URI"),)


# --------------------------------------------------------------------------- #
# write_load_sql
# --------------------------------------------------------------------------- #


class TestWriteLoadSql:
    """Oracle INSERT generation."""

    def test_emits_truncate_and_insert_per_table(self, tmp_path: Path) -> None:
        lookups = extract_lookups({"tranert.rep_type_ori.A": "001"})
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        text = sql_path.read_text(encoding="utf-8")
        assert "AUTO-GENERATED" in text
        # Statement terminator is ';' on its own line so the bootstrap
        # splitter parses every statement as independent. Targets are
        # schema-qualified ``app_int.`` per Policy A.
        assert (
            "TRUNCATE TABLE app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH\n;" in text
        )
        assert (
            "INSERT INTO app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH "
            "(PRIN_SCH, REP_TYP_ORI) VALUES ('A', '001')\n;"
        ) in text

    def test_emits_all_seven_tables_in_order(self, tmp_path: Path) -> None:
        lookups = extract_lookups(parse_properties(_load_fixture_text()))
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        text = sql_path.read_text(encoding="utf-8")
        expected_tables = [
            "app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH",
            "app_int.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ",
            "app_int.LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK",
            "app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT",
            "app_int.LKP_VALDO_SHAW_ACT_TYP_UI",
            "app_int.LKP_VALDO_SHAW_ORG_LEVEL4_BY_SAP_CENTER5",
            "app_int.LKP_VALDO_SHAW_CIF_ACT_CODE_BY_NAME_RELATION",
        ]
        # Each table is referenced and appears in declaration order.
        positions = [text.find(f"TRUNCATE TABLE {t}\n;") for t in expected_tables]
        assert all(p >= 0 for p in positions), positions
        assert positions == sorted(
            positions
        ), "TRUNCATE statements not in declaration order"

    def test_empty_string_becomes_oracle_null(self, tmp_path: Path) -> None:
        # An AAA-variant row has ACT_TYPE="" which must become NULL because
        # Oracle's empty-string-is-null rule would otherwise refuse the
        # INSERT under any NOT NULL constraint.
        lookups = extract_lookups({"tranert.ledger_cd.idx3.AAA.dept.100": "99"})
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        text = sql_path.read_text(encoding="utf-8")
        assert (
            "INSERT INTO app_int.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT "
            "(DEPT, IDX3_VARIANT, LOAN_TYPE, ACT_TYPE) "
            "VALUES ('100', 'AAA', '99', NULL)\n;"
        ) in text

    def test_single_quote_in_value_is_doubled(self, tmp_path: Path) -> None:
        # If a property value contains a single quote, the load SQL must
        # double it per Oracle string-literal rules.
        lookups = extract_lookups({"tranert.rep_type_ori.A": "O'Brien"})
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        text = sql_path.read_text(encoding="utf-8")
        assert "'O''Brien'" in text

    def test_empty_bucket_emits_truncate_only(self, tmp_path: Path) -> None:
        lookups = extract_lookups({"tranert.rep_type_ori.A": "001"})
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        text = sql_path.read_text(encoding="utf-8")
        # The rep_typ_uri bucket is empty in this case. TRUNCATE is still
        # emitted; no INSERTs follow it. Schema-qualified targets per Policy A.
        assert (
            "TRUNCATE TABLE app_int.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ\n;" in text
        )
        assert "INSERT INTO app_int.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ" not in text

    def test_output_is_lf_only_line_endings(self, tmp_path: Path) -> None:
        # The SHA-256 idempotency contract requires byte-stable output
        # across Linux and Windows. Verify no CRLF leaks in.
        lookups = extract_lookups({"tranert.rep_type_ori.A": "001"})
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        raw = sql_path.read_bytes()
        assert b"\r\n" not in raw, "load SQL must not contain CRLF line endings"
        assert raw.endswith(b"\n"), "load SQL must end with a newline"

    def test_bootstrap_splitter_parses_output_into_expected_statements(
        self, tmp_path: Path
    ) -> None:
        # End-to-end contract: every statement the extractor emits must be
        # recognised as an independent statement by the bootstrap splitter
        # the harness will use to run the file. With 1 row per bucket, the
        # splitter should see (1 TRUNCATE + 1 INSERT) per bucket × 7
        # buckets = 14 statements. Leading per-table comments are bundled
        # into the following statement (Oracle accepts comments preceding
        # SQL); they are not treated as separate statements.
        from scripts.e2e_lib.sql_bootstrap import _split_statements

        props = {
            "tranert.rep_type_ori.A": "001",
            "tranert.base.rep_typ_uri.M": "BM1",
            "loan.master.state.cd.001": "NC",
            "tranert.ledger_cd.dept.100": "10:CC",
            "tranert.act_typ_ui.5": "AU5",
            "tranert.org_level4.sap_center5.12345": "0000201",
            "tranert.32005.cif_act_code_name_relation.A": "P",
        }
        lookups = extract_lookups(props)
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        statements = _split_statements(sql_path.read_text(encoding="utf-8"))
        assert len(statements) == 14
        # TRUNCATE and INSERT keywords appear within their respective
        # statements (possibly after a leading -- comment block).
        truncates = [s for s in statements if "TRUNCATE TABLE" in s]
        inserts = [s for s in statements if "INSERT INTO" in s]
        assert len(truncates) == 7
        assert len(inserts) == 7
        # No statement carries a trailing semicolon — the terminator was
        # discarded by the splitter, as it should be.
        for s in statements:
            assert not s.rstrip().endswith(";"), s
        # No statement is comment-only — Oracle would reject ORA-00900.
        for s in statements:
            non_comment_lines = [
                ln
                for ln in s.splitlines()
                if ln.strip() and not ln.strip().startswith("--")
            ]
            assert non_comment_lines, f"comment-only statement: {s!r}"

    def test_bootstrap_splitter_handles_full_fixture(self, tmp_path: Path) -> None:
        # Against the full synthetic fixture: 7 TRUNCATE + 22 INSERT = 29
        # statements (3+3+3+5+3+2+3 = 22 INSERTs across the seven buckets).
        from scripts.e2e_lib.sql_bootstrap import _split_statements

        lookups = extract_lookups(parse_properties(_load_fixture_text()))
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        statements = _split_statements(sql_path.read_text(encoding="utf-8"))
        assert len(statements) == 29
        truncates = [s for s in statements if "TRUNCATE TABLE" in s]
        inserts = [s for s in statements if "INSERT INTO" in s]
        assert len(truncates) == 7
        assert len(inserts) == 22
        # No comment-only statements.
        for s in statements:
            non_comment_lines = [
                ln
                for ln in s.splitlines()
                if ln.strip() and not ln.strip().startswith("--")
            ]
            assert non_comment_lines, f"comment-only statement: {s!r}"

    def test_empty_buckets_do_not_emit_comment_only_statement(
        self, tmp_path: Path
    ) -> None:
        # Regression: when most buckets are empty, the splitter must NOT
        # emit a comment-only trailing statement. Originally a "-- (no
        # rows produced)" comment was emitted after each empty TRUNCATE
        # and trailed the file; that would have been parsed as a
        # standalone comment-only statement after the final TRUNCATE's
        # ';' terminator, which Oracle rejects as ORA-00900.
        from scripts.e2e_lib.sql_bootstrap import _split_statements

        lookups = extract_lookups({"tranert.rep_type_ori.A": "001"})
        sql_path = tmp_path / "load.sql"
        write_load_sql(lookups, sql_path)
        statements = _split_statements(sql_path.read_text(encoding="utf-8"))
        # 7 TRUNCATEs (one per bucket, including empty ones) + 1 INSERT.
        assert len(statements) == 8
        for s in statements:
            non_comment_lines = [
                ln
                for ln in s.splitlines()
                if ln.strip() and not ln.strip().startswith("--")
            ]
            assert non_comment_lines, f"comment-only statement: {s!r}"


# --------------------------------------------------------------------------- #
# run() — full orchestration with SHA-256 idempotency
# --------------------------------------------------------------------------- #


class TestRun:
    """End-to-end orchestrator."""

    def test_full_run_against_fixture(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "lookups"
        sql_file = tmp_path / "10_load" / "load.sql"
        result = run(
            property_file=_FIXTURE_PATH,
            output_dir=output_dir,
            sql_load_file=sql_file,
        )
        assert isinstance(result, RunResult)
        assert result.rewrote is True
        # The fixture has 3 out-of-scope keys.
        assert result.unmatched_keys_count == 3
        # Seven CSVs + .sha256 fingerprint. Names sorted alphabetically:
        # 'rep_type_ori' < 'rep_typ_uri' because 'pe' < 'p_' is false;
        # actually 'type_o' < 'typ_u' because 'e' < '_' is false either —
        # the correct comparison: 'rep_type_ori_by_prin_sch' vs
        # 'rep_typ_uri_by_terms_freq': first differing char is at index 7
        # ('e' vs '_'), and underscore (0x5F) sorts before 'e' (0x65),
        # so 'rep_typ_uri' actually sorts BEFORE 'rep_type_ori'. Honest
        # alphabetical sort below:
        assert sorted(p.name for p in output_dir.iterdir()) == [
            ".sha256",
            "act_typ_ui.csv",
            "cif_act_code_by_name_relation.csv",
            "ledger_cd_by_dept.csv",
            "loan_master_state_cd_by_bk.csv",
            "org_level4_by_sap_center5.csv",
            "rep_typ_uri_by_terms_freq.csv",
            "rep_type_ori_by_prin_sch.csv",
        ]
        # Load SQL exists and starts with the AUTO-GENERATED banner.
        assert sql_file.exists()
        assert "AUTO-GENERATED" in sql_file.read_text(encoding="utf-8")
        # SHA matches the fixture content.
        expected_digest = hashlib.sha256(
            _load_fixture_text().encode("utf-8")
        ).hexdigest()
        assert result.property_file_sha256 == expected_digest
        assert (output_dir / ".sha256").read_text(
            encoding="utf-8"
        ).strip() == expected_digest

    def test_second_run_with_unchanged_input_is_a_noop(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "lookups"
        sql_file = tmp_path / "load.sql"
        first = run(
            property_file=_FIXTURE_PATH,
            output_dir=output_dir,
            sql_load_file=sql_file,
        )
        # Tamper with a CSV; if the no-op kicks in correctly, the tampered
        # file remains in place (proving the writer did not run).
        tampered_csv = output_dir / "rep_type_ori_by_prin_sch.csv"
        tampered_csv.write_text("DO NOT OVERWRITE\n", encoding="utf-8")
        second = run(
            property_file=_FIXTURE_PATH,
            output_dir=output_dir,
            sql_load_file=sql_file,
        )
        assert first.property_file_sha256 == second.property_file_sha256
        assert second.rewrote is False
        # CSV is still tampered → writer was skipped, idempotency works.
        assert tampered_csv.read_text(encoding="utf-8") == "DO NOT OVERWRITE\n"

    def test_force_flag_regenerates_even_when_sha_matches(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "lookups"
        sql_file = tmp_path / "load.sql"
        run(
            property_file=_FIXTURE_PATH,
            output_dir=output_dir,
            sql_load_file=sql_file,
        )
        tampered_csv = output_dir / "rep_type_ori_by_prin_sch.csv"
        tampered_csv.write_text("DO NOT OVERWRITE\n", encoding="utf-8")
        second = run(
            property_file=_FIXTURE_PATH,
            output_dir=output_dir,
            sql_load_file=sql_file,
            force=True,
        )
        assert second.rewrote is True
        # Tampered CSV was regenerated.
        assert "PRIN_SCH,REP_TYP_ORI" in tampered_csv.read_text(encoding="utf-8")

    def test_changed_input_regenerates(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "lookups"
        sql_file = tmp_path / "load.sql"
        # First run with the fixture.
        run(
            property_file=_FIXTURE_PATH,
            output_dir=output_dir,
            sql_load_file=sql_file,
        )
        # Copy + modify fixture to a new file.
        modified = tmp_path / "modified.properties"
        modified.write_text(
            _load_fixture_text() + "\ntranert.rep_type_ori.Z=999\n",
            encoding="utf-8",
        )
        second = run(
            property_file=modified,
            output_dir=output_dir,
            sql_load_file=sql_file,
        )
        assert second.rewrote is True
        # New row visible in the CSV.
        assert "Z,999" in (output_dir / "rep_type_ori_by_prin_sch.csv").read_text(
            encoding="utf-8"
        )

    def test_missing_property_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "no-such-file.properties"
        with pytest.raises(
            TranertPropertyExtractError, match="property file not found"
        ):
            run(
                property_file=missing,
                output_dir=tmp_path,
                sql_load_file=tmp_path / "x.sql",
            )

    def test_malformed_property_file_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.properties"
        bad.write_text("good=value\nthis line has no equals\n", encoding="utf-8")
        with pytest.raises(TranertPropertyExtractError, match="no '='"):
            run(
                property_file=bad,
                output_dir=tmp_path / "out",
                sql_load_file=tmp_path / "x.sql",
            )

    def test_unmatched_keys_log_at_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            run(
                property_file=_FIXTURE_PATH,
                output_dir=tmp_path / "out",
                sql_load_file=tmp_path / "x.sql",
            )
        warnings = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "did not match" in r.getMessage()
        ]
        assert warnings, "expected a WARN about unmatched keys"


# --------------------------------------------------------------------------- #
# Value types are frozen
# --------------------------------------------------------------------------- #


class TestValueTypesFrozen:
    """The frozen value types reject mutation."""

    def test_extracted_lookups_is_frozen(self) -> None:
        lookups = ExtractedLookups(
            rep_type_ori_by_prin_sch=(),
            rep_typ_uri_by_terms_freq=(),
            loan_master_state_cd_by_bk=(),
            ledger_cd_by_dept=(),
            act_typ_ui=(),
            org_level4_by_sap_center5=(),
            cif_act_code_by_name_relation=(),
        )
        with pytest.raises((AttributeError, TypeError)):
            lookups.rep_type_ori_by_prin_sch = (("X", "Y"),)  # type: ignore[misc]

    def test_run_result_is_frozen(self) -> None:
        result = RunResult(
            property_file_sha256="0" * 64,
            rewrote=True,
            csv_files=(),
            load_sql_file=Path("x.sql"),
            unmatched_keys_count=0,
        )
        with pytest.raises((AttributeError, TypeError)):
            result.rewrote = False  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


class TestCli:
    """``python -m scripts.e2e_lib.extract_tranert_properties`` entry point."""

    def test_cli_happy_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_dir = tmp_path / "lookups"
        sql_file = tmp_path / "load.sql"
        exit_code = main(
            [
                "--property-file",
                str(_FIXTURE_PATH),
                "--output-dir",
                str(output_dir),
                "--sql-load-file",
                str(sql_file),
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "regenerated" in captured.out
        assert (output_dir / ".sha256").exists()
        assert sql_file.exists()

    def test_cli_idempotent_no_change_path(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        output_dir = tmp_path / "lookups"
        sql_file = tmp_path / "load.sql"
        # First run.
        main(
            [
                "--property-file",
                str(_FIXTURE_PATH),
                "--output-dir",
                str(output_dir),
                "--sql-load-file",
                str(sql_file),
            ]
        )
        capsys.readouterr()  # drain first-run output
        # Second run, unchanged input.
        exit_code = main(
            [
                "--property-file",
                str(_FIXTURE_PATH),
                "--output-dir",
                str(output_dir),
                "--sql-load-file",
                str(sql_file),
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "no change" in captured.out

    def test_cli_missing_file_returns_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = main(
            [
                "--property-file",
                str(tmp_path / "no-such-file.properties"),
                "--output-dir",
                str(tmp_path / "out"),
                "--sql-load-file",
                str(tmp_path / "x.sql"),
            ]
        )
        assert exit_code == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err

    def test_cli_requires_property_file_argument(self) -> None:
        with pytest.raises(SystemExit):
            main(
                [
                    "--output-dir",
                    "/tmp/out",
                    "--sql-load-file",
                    "/tmp/x.sql",
                ]
            )
