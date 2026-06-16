"""Unit tests for ``scripts.e2e_lib.shaw_tranert_smoke``.

Coverage is focused on the safety-critical layer: every classifier
allow/refuse path under Policy A (mandatory ``app_int.`` schema
qualification on every DDL/DML target), the audit logger, and the
orchestrator with a MagicMock-backed Oracle connection. No live Oracle
is required; all network paths are mocked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.shaw_tranert_smoke import (  # noqa: E402
    AuditLog,
    SmokeRunnerError,
    _render_reconcile_error_html,
    _render_reconcile_html,
    classify_statement,
    main,
)

# --------------------------------------------------------------------------- #
# Classifier — Policy A allowed paths
# --------------------------------------------------------------------------- #


class TestClassifyAllowed:
    """Statements the harness MUST accept under Policy A.

    Every DDL/DML target must be schema-qualified ``app_int.<object>``.
    SELECT statements are exempt — reading anywhere is always safe.
    """

    def test_plain_select(self) -> None:
        c = classify_statement("SELECT * FROM dual")
        assert c.is_safe is True
        assert c.kind == "select"

    def test_select_with_leading_line_comment(self) -> None:
        c = classify_statement("-- comment\nSELECT 1 FROM dual")
        assert c.is_safe is True
        assert c.kind == "select"

    def test_select_with_leading_block_comment(self) -> None:
        c = classify_statement("/* block */ SELECT 1 FROM dual")
        assert c.is_safe is True
        assert c.kind == "select"

    def test_select_against_source_system_table_is_allowed(self) -> None:
        # Read-only SELECT against the upstream Oracle schema. SELECT
        # has no target restriction because reading is always safe.
        c = classify_statement("SELECT * FROM app_int.SHAW_LOAN_MASTER")
        assert c.is_safe is True
        assert c.kind == "select"

    def test_create_table_schema_qualified(self) -> None:
        c = classify_statement(
            "CREATE TABLE app_int.LKP_VALDO_SHAW_FOO (PRIN_SCH VARCHAR2(10))"
        )
        assert c.is_safe is True
        assert c.kind == "create_table"
        assert c.target == "APP_INT.LKP_VALDO_SHAW_FOO"

    def test_create_table_schema_qualified_mixed_case_schema(self) -> None:
        # Oracle is case-insensitive for unquoted identifiers; the
        # classifier uppercases both schema and object. NOTE (S14-5, #416):
        # the mixed-case spelling of the harness schema APP_INT is "App_Int"
        # (the underscore is a literal identifier char). The previous example
        # "AppInt" was a genuinely different identifier — it uppercases to
        # APPINT, not APP_INT — so the classifier correctly refused it. That
        # refusal is correct security behaviour, so the test example is fixed
        # (not the classifier).
        c1 = classify_statement("CREATE TABLE APP_INT.LKP_VALDO_SHAW_FOO (X NUMBER)")
        assert c1.is_safe is True
        c2 = classify_statement("CREATE TABLE App_Int.LKP_VALDO_SHAW_FOO (X NUMBER)")
        assert c2.is_safe is True

    def test_create_or_replace_view_helper_prefix(self) -> None:
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_FOO " "AS SELECT 1 FROM dual"
        )
        assert c.is_safe is True
        assert c.kind == "create_view"
        assert c.target == "APP_INT.V_SHAW_TRANERT_FOO"

    def test_create_or_replace_view_expected_prefix(self) -> None:
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.EXPECTED_32000_VIEW " "AS SELECT 1 FROM dual"
        )
        assert c.is_safe is True
        assert c.kind == "create_view"
        assert c.target == "APP_INT.EXPECTED_32000_VIEW"

    def test_create_force_view_is_allowed(self) -> None:
        # FORCE skips dependency checking. Still a CREATE VIEW with a
        # app_int-qualified harness-prefixed target, so allowed.
        c = classify_statement(
            "CREATE OR REPLACE FORCE VIEW app_int.V_SHAW_TRANERT_BAR "
            "AS SELECT 1 FROM dual"
        )
        assert c.is_safe is True
        assert c.target == "APP_INT.V_SHAW_TRANERT_BAR"

    def test_truncate_schema_qualified(self) -> None:
        # Must be an allow-listed table (session-7 TRUNCATE allow-list).
        c = classify_statement(
            "TRUNCATE TABLE app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH"
        )
        assert c.is_safe is True
        assert c.kind == "truncate_table"
        assert c.target == "APP_INT.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH"

    def test_insert_schema_qualified(self) -> None:
        c = classify_statement(
            "INSERT INTO app_int.LKP_VALDO_SHAW_FOO (PRIN_SCH) VALUES ('A')"
        )
        assert c.is_safe is True
        assert c.kind == "insert"
        assert c.target == "APP_INT.LKP_VALDO_SHAW_FOO"

    def test_plsql_block_wrapping_schema_qualified_create_table(self) -> None:
        # The idempotent-DDL pattern from 010_lookup_tables.sql with
        # app_int.-qualified target.
        sql = """
BEGIN
  EXECUTE IMMEDIATE '
    CREATE TABLE app_int.LKP_VALDO_SHAW_BAR (X NUMBER)
  ';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
"""
        c = classify_statement(sql)
        assert c.is_safe is True
        assert c.kind == "plsql_block"
        assert c.target == "APP_INT.LKP_VALDO_SHAW_BAR"


# --------------------------------------------------------------------------- #
# Classifier — Policy A refused paths
# --------------------------------------------------------------------------- #


class TestClassifyRefused:
    """Statements the harness MUST refuse."""

    def test_update_is_refused(self) -> None:
        c = classify_statement("UPDATE app_int.SHAW_LOAN_MASTER SET CHG_OFF_CD='0'")
        assert c.is_safe is False
        assert "UPDATE" in c.reason

    def test_delete_is_refused(self) -> None:
        c = classify_statement("DELETE FROM app_int.SHAW_LOAN_MASTER")
        assert c.is_safe is False
        assert "DELETE" in c.reason

    def test_drop_table_is_refused(self) -> None:
        c = classify_statement("DROP TABLE app_int.LKP_VALDO_SHAW_FOO")
        assert c.is_safe is False
        assert "DROP" in c.reason

    def test_drop_view_is_refused(self) -> None:
        c = classify_statement("DROP VIEW app_int.V_SHAW_TRANERT_DRIVER")
        assert c.is_safe is False

    def test_merge_is_refused(self) -> None:
        c = classify_statement(
            "MERGE INTO app_int.LKP_VALDO_SHAW_FOO USING dual ON (1=1) "
            "WHEN MATCHED THEN UPDATE SET X=1"
        )
        assert c.is_safe is False
        assert "MERGE" in c.reason

    def test_alter_table_is_refused(self) -> None:
        c = classify_statement("ALTER TABLE app_int.LKP_VALDO_SHAW_FOO ADD (Y NUMBER)")
        assert c.is_safe is False

    def test_grant_is_refused(self) -> None:
        c = classify_statement("GRANT SELECT ON app_int.SHAW_LOAN_MASTER TO foo")
        assert c.is_safe is False


class TestClassifyPolicyAEnforcement:
    """Policy A: schema-qualification is mandatory on every harness target."""

    def test_bare_create_table_harness_prefix_is_refused(self) -> None:
        # Object name has the harness prefix, but no schema qualifier.
        # Policy A refuses this — Oracle would resolve LKP_VALDO_SHAW_FOO
        # against uzapp_ad0 (the connected user's default schema), which
        # is NOT where harness objects live.
        c = classify_statement("CREATE TABLE LKP_VALDO_SHAW_FOO (X NUMBER)")
        assert c.is_safe is False
        assert "not schema-qualified" in c.reason
        assert "app_int" in c.reason

    def test_bare_create_view_is_refused(self) -> None:
        c = classify_statement(
            "CREATE OR REPLACE VIEW V_SHAW_TRANERT_BAR AS SELECT 1 FROM dual"
        )
        assert c.is_safe is False
        assert "not schema-qualified" in c.reason

    def test_bare_truncate_is_refused(self) -> None:
        c = classify_statement("TRUNCATE TABLE LKP_VALDO_SHAW_FOO")
        assert c.is_safe is False
        assert "not schema-qualified" in c.reason

    def test_bare_insert_is_refused(self) -> None:
        c = classify_statement("INSERT INTO LKP_VALDO_SHAW_FOO (PRIN_SCH) VALUES ('A')")
        assert c.is_safe is False
        assert "not schema-qualified" in c.reason

    def test_bare_plsql_embedded_create_is_refused(self) -> None:
        sql = """
BEGIN
  EXECUTE IMMEDIATE 'CREATE TABLE LKP_VALDO_SHAW_BAR (X NUMBER)';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
"""
        c = classify_statement(sql)
        assert c.is_safe is False
        assert "not schema-qualified" in c.reason

    def test_wrong_schema_create_table_is_refused(self) -> None:
        c = classify_statement("CREATE TABLE uzapp_ad0.LKP_VALDO_SHAW_FOO (X NUMBER)")
        assert c.is_safe is False
        assert "not the harness schema" in c.reason

    def test_wrong_schema_create_view_is_refused(self) -> None:
        c = classify_statement(
            "CREATE OR REPLACE VIEW scott.V_SHAW_TRANERT_FOO " "AS SELECT 1 FROM dual"
        )
        assert c.is_safe is False
        assert "not the harness schema" in c.reason

    def test_wrong_schema_insert_is_refused(self) -> None:
        c = classify_statement(
            "INSERT INTO some_other.LKP_VALDO_SHAW_FOO (X) VALUES (1)"
        )
        assert c.is_safe is False
        assert "not the harness schema" in c.reason

    def test_correct_schema_wrong_object_prefix_is_refused(self) -> None:
        # app_int. qualification is right, but the object is not a
        # harness-owned table.
        c = classify_statement("CREATE TABLE app_int.SOME_OTHER_TABLE (X NUMBER)")
        assert c.is_safe is False
        assert "does not start with any allowed harness prefix" in c.reason

    def test_correct_schema_attempting_insert_on_source_table(self) -> None:
        c = classify_statement(
            "INSERT INTO app_int.SHAW_LOAN_MASTER (ACCT_NUM) VALUES ('123')"
        )
        assert c.is_safe is False
        assert "does not start with any allowed harness prefix" in c.reason


class TestClassifyCtasAndExpectedTables:
    """Shape (B), session 7: CTAS and the EXPECTED_/helper table prefixes.

    Because the app_int account lacks CREATE VIEW (ORA-01031), the L2b
    result sets are materialized as app_int-owned tables via
    CREATE TABLE ... AS SELECT and refreshed via TRUNCATE + INSERT ...
    SELECT. The classifier must accept CTAS for the harness table prefixes
    and report a distinct ``create_table_as_select`` kind.
    """

    def test_ctas_helper_table_is_allowed_and_marked_ctas(self) -> None:
        c = classify_statement(
            "CREATE TABLE app_int.V_SHAW_TRANERT_DRIVER AS "
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE 1 = 0"
        )
        assert c.is_safe is True
        assert c.kind == "create_table_as_select"
        assert c.target == "APP_INT.V_SHAW_TRANERT_DRIVER"

    def test_ctas_expected_table_is_allowed(self) -> None:
        c = classify_statement(
            "CREATE TABLE app_int.EXPECTED_32000_TBL AS "
            "SELECT * FROM app_int.V_SHAW_TRANERT_DRIVER WHERE 1 = 0"
        )
        assert c.is_safe is True
        assert c.kind == "create_table_as_select"
        assert c.target == "APP_INT.EXPECTED_32000_TBL"

    def test_ctas_with_leading_with_clause_marked_ctas(self) -> None:
        c = classify_statement(
            "CREATE TABLE app_int.V_SHAW_TRANERT_COST_MERGED AS "
            "SELECT * FROM (WITH x AS (SELECT 1 AS n FROM dual) "
            "SELECT n FROM x) WHERE 1 = 0"
        )
        assert c.is_safe is True
        assert c.kind == "create_table_as_select"

    def test_plain_create_table_still_marked_create_table(self) -> None:
        c = classify_statement(
            "CREATE TABLE app_int.V_SHAW_TRANERT_DRIVER (ACCT_NUM VARCHAR2(10))"
        )
        assert c.is_safe is True
        assert c.kind == "create_table"

    def test_ctas_wrong_prefix_is_refused(self) -> None:
        c = classify_statement(
            "CREATE TABLE app_int.SOME_OTHER_TBL AS SELECT 1 AS n FROM dual"
        )
        assert c.is_safe is False
        assert "does not start with any allowed harness prefix" in c.reason

    def test_ctas_bare_identifier_is_refused(self) -> None:
        c = classify_statement(
            "CREATE TABLE V_SHAW_TRANERT_DRIVER AS SELECT 1 AS n FROM dual"
        )
        assert c.is_safe is False
        assert "not schema-qualified" in c.reason

    def test_insert_select_into_helper_table_allowed(self) -> None:
        c = classify_statement(
            "INSERT INTO app_int.V_SHAW_TRANERT_DRIVER "
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE CHG_OFF_CD = '1'"
        )
        assert c.is_safe is True
        assert c.kind == "insert"

    def test_insert_into_expected_table_allowed(self) -> None:
        c = classify_statement(
            "INSERT INTO app_int.EXPECTED_32000_TBL (ACCT_NUM) "
            "SELECT ACCT_NUM FROM app_int.V_SHAW_TRANERT_DRIVER"
        )
        assert c.is_safe is True
        assert c.kind == "insert"

    def test_plsql_embedded_ctas_helper_table_allowed(self) -> None:
        # The idempotent ORA-00955-trapping block wrapping a CTAS, exactly
        # as 030_expected_tables.sql emits it (q'[ ]' alternative quoting).
        sql = """
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.V_SHAW_TRANERT_BK1 AS
      SELECT * FROM (SELECT '1' AS x FROM dual) WHERE 1 = 0
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
"""
        c = classify_statement(sql)
        assert c.is_safe is True
        assert c.kind == "plsql_block"
        assert c.target == "APP_INT.V_SHAW_TRANERT_BK1"


class TestClassifyTruncateAllowlist:
    """TRUNCATE is gated by the explicit _TRUNCATE_ALLOWLIST (option (a))."""

    def test_allowlisted_lookup_truncate_allowed(self) -> None:
        c = classify_statement(
            "TRUNCATE TABLE app_int.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH"
        )
        assert c.is_safe is True
        assert c.kind == "truncate_table"

    def test_allowlisted_helper_truncate_allowed(self) -> None:
        c = classify_statement("TRUNCATE TABLE app_int.V_SHAW_TRANERT_DRIVER")
        assert c.is_safe is True
        assert c.kind == "truncate_table"

    def test_prefix_valid_but_not_allowlisted_is_refused(self) -> None:
        # Correct schema + valid harness prefix, but the specific name is
        # NOT in the allow-list. Must still be refused.
        c = classify_statement("TRUNCATE TABLE app_int.LKP_VALDO_SHAW_NOT_REAL")
        assert c.is_safe is False
        assert "not in the TRUNCATE allow-list" in c.reason

    def test_expected_tables_truncate_allowed(self) -> None:
        # The full per-record-type expected-table set: simple types (5b1),
        # the per-contact 32005 (5b2a), and the remaining complex types
        # 32010 + 32025 (5b2b). All are in the allow-list AND the bootstrap
        # SQL.
        for name in (
            "EXPECTED_BATCH_HEADER_TBL",
            "EXPECTED_32000_TBL",
            "EXPECTED_32005_TBL",
            "EXPECTED_32010_TBL",
            "EXPECTED_32025_TBL",
            "EXPECTED_32040_TBL",
            "EXPECTED_32075_TBL",
        ):
            c = classify_statement(f"TRUNCATE TABLE app_int.{name}")
            assert c.is_safe is True, name
            assert c.kind == "truncate_table"

    def test_unknown_expected_prefix_table_is_refused(self) -> None:
        # An EXPECTED_-prefixed table that is NOT in the allow-list is still
        # refused: the prefix alone never grants TRUNCATE; the explicit
        # allow-list (kept in sync with the bootstrap CREATE set by the drift
        # test) is the gate.
        c = classify_statement("TRUNCATE TABLE app_int.EXPECTED_99999_TBL")
        assert c.is_safe is False
        assert "not in the TRUNCATE allow-list" in c.reason

    def test_source_registry_truncate_allowed(self) -> None:
        # The cross-source TRANERT dimension is allow-listed.
        c = classify_statement(
            "TRUNCATE TABLE app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY"
        )
        assert c.is_safe is True
        assert c.kind == "truncate_table"


class TestClassifyTranertLookupPrefix:
    """The generalized LKP_VALDO_TRANERT_ prefix (cross-source dimensions)."""

    def test_create_table_tranert_lookup_allowed(self) -> None:
        c = classify_statement(
            "CREATE TABLE app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY "
            "(LOCATION_CODE VARCHAR2(24))"
        )
        assert c.is_safe is True
        assert c.kind == "create_table"
        assert c.target == "APP_INT.LKP_VALDO_TRANERT_SOURCE_REGISTRY"

    def test_insert_into_tranert_lookup_allowed(self) -> None:
        c = classify_statement(
            "INSERT INTO app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY "
            "(LOCATION_CODE, ACTG_SYS_ID) VALUES ('100030', 'LS')"
        )
        assert c.is_safe is True
        assert c.kind == "insert"

    def test_plsql_embedded_ctas_tranert_lookup_allowed(self) -> None:
        sql = """
BEGIN
  EXECUTE IMMEDIATE q'[
    CREATE TABLE app_int.LKP_VALDO_TRANERT_SOURCE_REGISTRY (
      LOCATION_CODE VARCHAR2(24) NOT NULL
    )
  ]';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;
END;
"""
        c = classify_statement(sql)
        assert c.is_safe is True
        assert c.kind == "plsql_block"
        assert c.target == "APP_INT.LKP_VALDO_TRANERT_SOURCE_REGISTRY"


class TestTruncateAllowlistDrift:
    """The allow-list must match the tables actually declared in bootstrap SQL.

    CI fails if a table is added to one without the other, preventing the
    allow-list from silently drifting out of sync with the harness DDL.
    """

    def test_truncate_allowlist_matches_bootstrap_sql(self) -> None:
        import re

        from scripts.e2e_lib import shaw_tranert_smoke as mod

        tranert = mod._TRANERT_DIR
        lookup_sql = (tranert / "00_bootstrap" / "010_lookup_tables.sql").read_text(
            encoding="utf-8"
        )
        registry_sql = (tranert / "00_bootstrap" / "040_source_registry.sql").read_text(
            encoding="utf-8"
        )
        expected_sql = (tranert / "00_bootstrap" / "030_expected_tables.sql").read_text(
            encoding="utf-8"
        )

        # Every CREATE TABLE app_int.<name> across all bootstrap DDL files.
        declared = set()
        for text in (lookup_sql, registry_sql, expected_sql):
            for schema, obj in re.findall(
                r"CREATE\s+TABLE\s+"
                r"(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)",
                text,
                flags=re.IGNORECASE,
            ):
                if schema:
                    declared.add(f"{schema.upper()}.{obj.upper()}")

        # The allow-list and the declared-tables set must be identical. If
        # this fails, either a CREATE TABLE was added without allow-listing
        # it (would break the refresh), or an allow-list entry has no
        # backing CREATE (a stale/typo entry).
        assert declared == set(mod._TRUNCATE_ALLOWLIST), (
            "TRUNCATE allow-list drifted from bootstrap SQL.\n"
            f"declared-but-not-allowlisted: {sorted(declared - set(mod._TRUNCATE_ALLOWLIST))}\n"
            f"allowlisted-but-not-declared: {sorted(set(mod._TRUNCATE_ALLOWLIST) - declared)}"
        )


class TestClassifyMisc:
    """Edge cases."""

    def test_plsql_block_with_no_create_table_is_refused(self) -> None:
        sql = """
BEGIN
  UPDATE app_int.SHAW_LOAN_MASTER SET CHG_OFF_CD = '0';
END;
"""
        c = classify_statement(sql)
        assert c.is_safe is False
        assert "no recognisable CREATE TABLE" in c.reason

    def test_comment_only_statement_is_refused(self) -> None:
        c = classify_statement("-- only a comment\n")
        assert c.is_safe is False

    def test_empty_statement_is_refused(self) -> None:
        c = classify_statement("")
        assert c.is_safe is False

    def test_lowercase_select(self) -> None:
        c = classify_statement("select 1 from dual")
        assert c.is_safe is True
        assert c.kind == "select"

    def test_mixed_case_create_table(self) -> None:
        c = classify_statement("CrEaTe TaBlE app_int.lkp_valdo_shaw_FOO (X NUMBER)")
        assert c.is_safe is True
        assert c.target == "APP_INT.LKP_VALDO_SHAW_FOO"


class TestClassifyCreateViewBindVarRejection:
    """Oracle refuses CREATE VIEW with bind variables (ORA-01027).

    The classifier catches this at the gate so the failure surfaces as
    a refusal with a clear reason, not as an opaque Oracle error
    mid-bootstrap. Regression coverage for the bug that surfaced when
    a parameterised V_SHAW_TRANERT_COST2 view was attempted against SIT
    during issue #18.
    """

    def test_create_view_with_named_bind_refused(self) -> None:
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_FOO AS "
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE acct_num = :acct"
        )
        assert c.is_safe is False
        assert "ORA-01027" in c.reason
        assert ":acct" in c.reason

    def test_create_view_with_multiple_binds_refused_on_first(self) -> None:
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_FOO AS "
            "SELECT * FROM app_int.SHAW_LOAN_MASTER "
            "WHERE batch_date = :batch_date AND r = :rank_offset"
        )
        assert c.is_safe is False
        assert "ORA-01027" in c.reason
        # The reason cites whichever bind was matched first; both must
        # appear in the SQL body for this case to make sense.
        assert ":batch_date" in c.reason or ":rank_offset" in c.reason

    def test_create_view_without_binds_still_allowed(self) -> None:
        # Regression: the bind-var check must not over-fire on bind-free
        # view bodies. The seven helper views in 020_expected_views.sql
        # have no binds and must remain safe.
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_BAR AS "
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE chg_off_cd = '1'"
        )
        assert c.is_safe is True
        assert c.kind == "create_view"

    def test_create_view_with_plsql_type_cast_double_colon_allowed(self) -> None:
        # Defence: ``::TYPE`` casts use two colons and must NOT match the
        # bind-variable regex. Oracle does not actually use ::TYPE
        # syntax (that's Postgres), but the regex is defensive against
        # accidental adoption.
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_BAZ AS "
            "SELECT acct_num::VARCHAR2 FROM app_int.SHAW_LOAN_MASTER"
        )
        assert c.is_safe is True

    def test_select_with_bind_is_still_allowed(self) -> None:
        # The bind-variable check only fires for CREATE VIEW. SELECTs
        # with binds are normal (the cost2.sql query uses :batch_date
        # and :rank_offset) and must remain safe.
        c = classify_statement(
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE batch_date = :batch_date"
        )
        assert c.is_safe is True
        assert c.kind == "select"

    def test_insert_with_bind_is_still_allowed(self) -> None:
        # INSERT with binds is the canonical safe operation; not the
        # target of the ORA-01027 check.
        c = classify_statement(
            "INSERT INTO app_int.LKP_VALDO_SHAW_FOO (X) VALUES (:val)"
        )
        assert c.is_safe is True
        assert c.kind == "insert"

    def test_create_view_with_bind_only_in_line_comment_allowed(self) -> None:
        # Bind-like text inside a -- comment is not seen by Oracle's
        # parser and must not trigger a refusal. The classifier strips
        # comments before the bind-detection regex runs.
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_QUX AS\n"
            "-- example call site uses :batch_date and :rank_offset binds\n"
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE chg_off_cd = '1'"
        )
        assert c.is_safe is True
        assert c.kind == "create_view"

    def test_create_view_with_bind_only_in_block_comment_allowed(self) -> None:
        # Same protection for /* ... */ block comments.
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_QUX AS\n"
            "/* TODO: future variant might accept :batch_date binds */\n"
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE chg_off_cd = '1'"
        )
        assert c.is_safe is True
        assert c.kind == "create_view"

    def test_create_view_with_colon_inside_string_literal_allowed(self) -> None:
        # A colon inside a single-quoted string literal is part of the
        # string, not a bind. Oracle does not treat it as one, and
        # neither should we.
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_QUX AS\n"
            "SELECT 'http://example.com:8080' AS url FROM dual"
        )
        assert c.is_safe is True
        assert c.kind == "create_view"

    def test_create_view_with_doubled_apostrophe_inside_string_allowed(self) -> None:
        # Defence against the comment-stripper miscounting quotes when
        # an Oracle doubled-apostrophe escape (''') appears inside a
        # string literal.
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_QUX AS\n"
            "SELECT 'O''Brien' AS name FROM dual"
        )
        assert c.is_safe is True
        assert c.kind == "create_view"

    def test_create_view_bind_in_body_after_comment_still_refused(self) -> None:
        # Regression: stripping comments must not also strip real binds.
        c = classify_statement(
            "CREATE OR REPLACE VIEW app_int.V_SHAW_TRANERT_QUX AS\n"
            "-- this is a comment\n"
            "SELECT * FROM app_int.SHAW_LOAN_MASTER WHERE acct_num = :acct"
        )
        assert c.is_safe is False
        assert "ORA-01027" in c.reason


# --------------------------------------------------------------------------- #
# AuditLog
# --------------------------------------------------------------------------- #


class TestAuditLog:
    """JSONL audit-log writer."""

    def test_emit_writes_one_line_per_event(self, tmp_path: Path) -> None:
        log = AuditLog(path=tmp_path / "audit.jsonl")
        log.emit("alpha", stmt_kind="select")
        log.emit("beta", stmt_kind="create_table", target="X")
        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        a = json.loads(lines[0])
        b = json.loads(lines[1])
        assert a["event"] == "alpha"
        assert a["stmt_kind"] == "select"
        assert b["event"] == "beta"
        assert b["target"] == "X"

    def test_emit_redacts_password_like_field_names(self, tmp_path: Path) -> None:
        log = AuditLog(path=tmp_path / "audit.jsonl")
        log.emit("connect_attempt", password="hunter2", oracle_secret="abc")
        line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
        record = json.loads(line)
        assert record["password"] == "<redacted>"
        assert record["oracle_secret"] == "<redacted>"

    def test_emit_serialises_path_via_default(self, tmp_path: Path) -> None:
        log = AuditLog(path=tmp_path / "audit.jsonl")
        log.emit("file_starting", source_file=tmp_path / "x.sql")
        line = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
        record = json.loads(line)
        assert "source_file" in record
        assert "x.sql" in record["source_file"]

    def test_constructor_truncates_existing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "audit.jsonl"
        path.write_text("preexisting\nstale\n", encoding="utf-8")
        log = AuditLog(path=path)
        assert path.read_text(encoding="utf-8") == ""
        log.emit("event1")
        assert path.read_text(encoding="utf-8").count("\n") == 1


# --------------------------------------------------------------------------- #
# main() — argparse-level wiring (no Oracle)
# --------------------------------------------------------------------------- #


class TestCliArgparse:
    """The CLI parser surface."""

    def test_no_subcommand_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main([])

    def test_unknown_subcommand_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["wat"])

    def test_query_requires_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit):
            main(["query"])

    def test_query_rejects_illegal_path_chars(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        monkeypatch.setattr(
            mod,
            "_new_audit_log",
            lambda: mod.AuditLog(path=tmp_path / "a.jsonl"),
        )
        rc = main(["query", "../etc/passwd"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "illegal path chars" in captured.err


# --------------------------------------------------------------------------- #
# Orchestration with mocked Oracle
# --------------------------------------------------------------------------- #


def _mk_conn() -> MagicMock:
    """Build a MagicMock that behaves like a PEP-249 connection."""
    conn = MagicMock(name="conn")
    cursor = MagicMock(name="cursor")
    conn.cursor.return_value = cursor
    return conn


class TestRunBootstrapMocked:
    """``bootstrap`` orchestration against a MagicMock Oracle connection."""

    def test_bootstrap_executes_all_six_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        conn = _mk_conn()
        monkeypatch.setattr(mod, "_connect_sit", lambda **kwargs: conn)
        audit = mod.AuditLog(path=tmp_path / "audit.jsonl")
        mod.run_bootstrap(audit)
        # Shape (B), session 7. Six bootstrap files now run:
        #   1. 010_lookup_tables.sql         — 7 PL/SQL CREATE-TABLE blocks
        #   2. 040_source_registry.sql       — 1 PL/SQL CREATE-TABLE block
        #      (cross-source LKP_VALDO_TRANERT_SOURCE_REGISTRY dimension)
        #   3. 010_load_lookups_from_csv.sql — 168 TRUNCATE/INSERT ... VALUES
        #   4. 030_refresh_source_registry.sql — 1 TRUNCATE + 18 INSERT ... VALUES
        #   5. 030_expected_tables.sql       — 10 PL/SQL CTAS blocks
        #      (the 10 helper tables, formerly V_SHAW_TRANERT_* views)
        #   6. 020_refresh_expected.sql      — 10 TRUNCATE + 10 INSERT ... SELECT
        # The former 020_expected_views.sql (CREATE VIEW) is no longer in the
        # bootstrap order — the app_int account lacks CREATE VIEW (ORA-01031),
        # so the helper result sets are materialized as tables instead.
        # >= keeps this forward-compatible with the 5b EXPECTED_*_TBL additions.
        assert conn.cursor.return_value.execute.call_count >= 7 + 1 + 168 + 19 + 10 + 20
        conn.commit.assert_called_once()
        audit_lines = (
            (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        )
        events = [json.loads(line)["event"] for line in audit_lines]
        assert events.count("file_starting") == 6
        assert events.count("file_completed") == 6
        assert events.count("bootstrap_committed") == 1

    def test_bootstrap_refuses_when_a_statement_fails_classification(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        bad = tmp_path / "00_bootstrap" / "010_lookup_tables.sql"
        bad.parent.mkdir(parents=True)
        # DROP is refused regardless of schema-qualification.
        bad.write_text("DROP TABLE app_int.LKP_VALDO_SHAW_FOO\n;\n", encoding="utf-8")
        monkeypatch.setattr(mod, "_bootstrap_files_in_order", lambda: [bad])
        conn = _mk_conn()
        monkeypatch.setattr(mod, "_connect_sit", lambda **kwargs: conn)
        audit = mod.AuditLog(path=tmp_path / "audit.jsonl")
        with pytest.raises(SmokeRunnerError, match="refused statement"):
            mod.run_bootstrap(audit)
        conn.cursor.return_value.execute.assert_not_called()
        conn.commit.assert_not_called()


class TestRunValidateMocked:
    """``validate`` orchestration."""

    def test_validate_probes_every_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        conn = _mk_conn()
        cursor = conn.cursor.return_value
        cursor.fetchmany.return_value = []
        monkeypatch.setattr(mod, "_connect_sit", lambda **kwargs: conn)
        audit = mod.AuditLog(path=tmp_path / "audit.jsonl")
        results = mod.run_validate(audit)
        # Shape (B), session 7: validate now probes the 10 materialized
        # helper tables in 030_expected_tables.sql (formerly the
        # V_SHAW_TRANERT_* helper views). Forward-compatible with >= so the
        # 5b EXPECTED_*_TBL additions don't break this test.
        assert len(results) >= 10
        assert all(status == "ok" for status in results.values())
        for call in cursor.execute.call_args_list:
            sql = call.args[0]
            assert sql.startswith("SELECT *")
            assert "WHERE ROWNUM <= 1" in sql
            # Probes are schema-qualified app_int.<table>, mirroring the CTAS
            # emission in 030_expected_tables.sql.
            assert "APP_INT." in sql.upper()


class TestRunQueryMocked:
    """``query <name>`` orchestration."""

    def test_query_runs_existing_wrapper(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        conn = _mk_conn()
        cursor = conn.cursor.return_value
        cursor.fetchmany.return_value = [("A", "001"), ("B", "002")]
        monkeypatch.setattr(mod, "_connect_sit", lambda **kwargs: conn)
        audit = mod.AuditLog(path=tmp_path / "audit.jsonl")
        rows = mod.run_query("tranert_driver", limit=5, audit=audit)
        assert rows == [("A", "001"), ("B", "002")]
        cursor.fetchmany.assert_called_once_with(5)

    def test_query_unknown_name_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        conn = _mk_conn()
        monkeypatch.setattr(mod, "_connect_sit", lambda **kwargs: conn)
        audit = mod.AuditLog(path=tmp_path / "audit.jsonl")
        with pytest.raises(SmokeRunnerError, match="query file not found"):
            mod.run_query("no_such_query", limit=5, audit=audit)


# --------------------------------------------------------------------------- #
# Connection error path
# --------------------------------------------------------------------------- #


class TestConnectionErrors:
    """`_connect_sit` error wrapping.

    Cannot test by deleting env vars because ``secret_resolver`` calls
    ``load_dotenv`` at import time, which re-populates os.environ from
    the repo-root .env file. Instead we monkeypatch the provider's
    fetch method to simulate "secret not set".
    """

    def test_missing_dsn_credential_raises_smokerunnererror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import secret_resolver
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        monkeypatch.setattr(
            secret_resolver.EnvSecretProvider,
            "fetch",
            lambda self, name: None,
        )

        with pytest.raises(SmokeRunnerError, match="missing SIT credential"):
            mod._connect_sit()


# --------------------------------------------------------------------------- #
# app_int connection mechanism (--as-app_int)
# --------------------------------------------------------------------------- #


class TestConnectAsApp_int:
    """`_connect_sit(as_app_int=True)` credential selection and DSN fallback.

    The app_int path reads ORACLE_USER_SIT_APP_INT / ORACLE_PASSWORD_SIT_APP_INT
    and prefers ORACLE_DSN_SIT_APP_INT, falling back to ORACLE_DSN_SIT when
    the app_int-specific DSN is unset/blank (same SIT host/service, different
    login). Connecting as app_int resolves the cross-schema view-privilege
    issue on #18 (ORA-01031).

    The oracledb driver is stubbed via a fake module injected into
    sys.modules so no real Oracle is required and the exact connect()
    kwargs can be asserted.
    """

    @staticmethod
    def _install_fake_oracledb(
        monkeypatch: pytest.MonkeyPatch,
    ) -> "List[dict]":
        """Inject a fake ``oracledb`` module; return a list capturing connect kwargs."""
        import types

        captured: List[dict] = []

        def _fake_connect(**kwargs: object) -> object:
            captured.append(dict(kwargs))
            return MagicMock(name="fake_conn")

        fake = types.ModuleType("oracledb")
        fake.connect = _fake_connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "oracledb", fake)
        return captured

    def test_as_app_int_uses_app_int_user_and_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import secret_resolver
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        captured = self._install_fake_oracledb(monkeypatch)
        env = {
            "ORACLE_DSN_SIT_APP_INT": "host:1521/APP_SVC",
            "ORACLE_USER_SIT_APP_INT": "app_int",
            "ORACLE_PASSWORD_SIT_APP_INT": "app_pw",
            "ORACLE_DSN_SIT": "host:1521/APPSSIT1",
            "ORACLE_USER_SIT": "uzapp_ad0",
            "ORACLE_PASSWORD_SIT": "uzpw",
        }
        monkeypatch.setattr(
            secret_resolver.EnvSecretProvider,
            "fetch",
            lambda self, name: env.get(name),
        )

        mod._connect_sit(as_app_int=True)

        assert len(captured) == 1
        assert captured[0]["user"] == "app_int"
        assert captured[0]["password"] == "app_pw"
        # app_int-specific DSN is honoured when present.
        assert captured[0]["dsn"] == "host:1521/APP_SVC"

    def test_as_app_int_falls_back_to_shared_dsn_when_app_int_dsn_blank(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import secret_resolver
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        captured = self._install_fake_oracledb(monkeypatch)
        # ORACLE_DSN_SIT_APP_INT is blank -> fall back to ORACLE_DSN_SIT.
        env = {
            "ORACLE_DSN_SIT_APP_INT": "",
            "ORACLE_USER_SIT_APP_INT": "app_int",
            "ORACLE_PASSWORD_SIT_APP_INT": "app_pw",
            "ORACLE_DSN_SIT": "SIT-PCOLORA01:1521/APPSSIT1",
            "ORACLE_USER_SIT": "uzapp_ad0",
            "ORACLE_PASSWORD_SIT": "uzpw",
        }
        monkeypatch.setattr(
            secret_resolver.EnvSecretProvider,
            "fetch",
            lambda self, name: env.get(name),
        )

        mod._connect_sit(as_app_int=True)

        assert captured[0]["user"] == "app_int"
        assert captured[0]["dsn"] == "SIT-PCOLORA01:1521/APPSSIT1"

    def test_as_app_int_missing_user_raises_app_int_specific_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import secret_resolver
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        self._install_fake_oracledb(monkeypatch)
        # app_int user/password unset -> app_int-specific error message.
        env = {"ORACLE_DSN_SIT": "host:1521/APPSSIT1"}
        monkeypatch.setattr(
            secret_resolver.EnvSecretProvider,
            "fetch",
            lambda self, name: env.get(name),
        )

        with pytest.raises(SmokeRunnerError, match="missing app_int SIT credential"):
            mod._connect_sit(as_app_int=True)

    def test_default_path_still_uses_uzapp_ad0(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import secret_resolver
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        captured = self._install_fake_oracledb(monkeypatch)
        env = {
            "ORACLE_USER_SIT_APP_INT": "app_int",
            "ORACLE_PASSWORD_SIT_APP_INT": "app_pw",
            "ORACLE_DSN_SIT": "host:1521/APPSSIT1",
            "ORACLE_USER_SIT": "uzapp_ad0",
            "ORACLE_PASSWORD_SIT": "uzpw",
        }
        monkeypatch.setattr(
            secret_resolver.EnvSecretProvider,
            "fetch",
            lambda self, name: env.get(name),
        )

        mod._connect_sit()  # as_app_int defaults to False

        assert captured[0]["user"] == "uzapp_ad0"
        assert captured[0]["password"] == "uzpw"


class TestApp_intFlagThreading:
    """The --as-app_int CLI flag must reach _connect_sit and the audit log."""

    def test_bootstrap_flag_threads_to_connect_and_audit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        seen: List[bool] = []

        def _fake_connect(*, as_app_int: bool = False) -> MagicMock:
            seen.append(as_app_int)
            return _mk_conn()

        monkeypatch.setattr(mod, "_connect_sit", _fake_connect)
        monkeypatch.setattr(
            mod,
            "_new_audit_log",
            lambda: mod.AuditLog(path=tmp_path / "audit.jsonl"),
        )

        rc = main(["--as-app_int", "bootstrap"])
        assert rc == 0
        assert seen == [True]

        events = [
            json.loads(line)
            for line in (tmp_path / "audit.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        identity = [e for e in events if e["event"] == "connection_identity"]
        assert len(identity) == 1
        assert identity[0]["as_app_int"] is True

    def test_bootstrap_without_flag_connects_as_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.e2e_lib import shaw_tranert_smoke as mod

        seen: List[bool] = []

        def _fake_connect(*, as_app_int: bool = False) -> MagicMock:
            seen.append(as_app_int)
            return _mk_conn()

        monkeypatch.setattr(mod, "_connect_sit", _fake_connect)
        monkeypatch.setattr(
            mod,
            "_new_audit_log",
            lambda: mod.AuditLog(path=tmp_path / "audit.jsonl"),
        )

        rc = main(["bootstrap"])
        assert rc == 0
        assert seen == [False]


# --------------------------------------------------------------------------- #
# Reconcile HTML renderers — pure, no Oracle
# --------------------------------------------------------------------------- #


class _StubSpec:
    """Minimal stand-in for ReconciliationSpec (source/file_type only)."""

    source = "SHAW"
    file_type = "TRANERT"


class _StubPerType:
    """Duck-typed PerTypeCount."""

    def __init__(self, file_rows: int, expected_rows: int, field_mismatches: int):
        self.file_rows = file_rows
        self.expected_rows = expected_rows
        self.field_mismatches = field_mismatches


class _StubViolation:
    """Duck-typed ReconciliationViolation."""

    def __init__(self, kind, record_type, key_values, field, expected, actual,
                 message, line_number):
        self.kind = kind
        self.record_type = record_type
        self.key_values = key_values
        self.field = field
        self.expected = expected
        self.actual = actual
        self.message = message
        self.line_number = line_number


class _StubReport:
    """Duck-typed ReconciliationReport."""

    def __init__(self, rows_compared, rows_unknown_type, violations, per_type_counts):
        self.rows_compared = rows_compared
        self.rows_unknown_type = rows_unknown_type
        self.violations = violations
        self.per_type_counts = per_type_counts


class TestReconcileRenderers:
    """The HTML renderers are pure functions and must produce valid markup."""

    def test_clean_report_renders_pass(self) -> None:
        report = _StubReport(
            rows_compared=7,
            rows_unknown_type=0,
            violations=[],
            per_type_counts={"rt_32000": _StubPerType(1, 1, 0)},
        )
        doc = _render_reconcile_html(
            report, Path("_ref/sample.txt"), _StubSpec(), "20260101T000000Z"
        )
        assert "<!doctype html>" in doc
        assert "PASS" in doc
        assert "No violations" in doc
        # balanced top-level structure
        assert doc.count("<div") == doc.count("</div>")
        assert doc.count("<table") == doc.count("</table>")

    def test_report_with_violation_renders_fail(self) -> None:
        viol = _StubViolation(
            kind="field_mismatch",
            record_type="rt_32000",
            key_values=("0190874915171001",),
            field="LCT-COD-NEW1",
            expected="100030",
            actual="100099",
            message="value differs",
            line_number=2,
        )
        report = _StubReport(
            rows_compared=7,
            rows_unknown_type=0,
            violations=[viol],
            per_type_counts={"rt_32000": _StubPerType(1, 1, 1)},
        )
        doc = _render_reconcile_html(
            report, Path("_ref/sample.txt"), _StubSpec(), "20260101T000000Z"
        )
        assert "FAIL" in doc
        assert "field_mismatch" in doc
        assert "LCT-COD-NEW1" in doc
        assert "100030" in doc and "100099" in doc

    def test_renderer_escapes_html(self) -> None:
        viol = _StubViolation(
            kind="field_mismatch",
            record_type="rt_x",
            key_values=("k",),
            field="F",
            expected="<script>alert(1)</script>",
            actual="&amp;",
            message="m",
            line_number=1,
        )
        report = _StubReport(1, 0, [viol], {"rt_x": _StubPerType(1, 1, 1)})
        doc = _render_reconcile_html(
            report, Path("f.txt"), _StubSpec(), "ts"
        )
        assert "<script>alert(1)</script>" not in doc
        assert "&lt;script&gt;" in doc

    def test_error_report_renders_fail_with_message(self) -> None:
        doc = _render_reconcile_error_html(
            "unknown record type at line 4: discriminator='00000'",
            Path("_ref/sample.txt"),
            _StubSpec(),
            "20260101T000000Z",
        )
        assert "<!doctype html>" in doc
        assert "FAIL - reconcile aborted" in doc
        assert "discriminator" in doc
        assert doc.count("<div") == doc.count("</div>")
