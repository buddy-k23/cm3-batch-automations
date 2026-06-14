"""SHAW TRANERT SIT smoke-runner: connect to Oracle, run the harness SQL.

Purpose
-------
The L2b SQL-Truth gate work (#18) authors Oracle SQL artifacts under
``config/e2e/sources/SHAW/sql/tranert/``. This module provides the
read-only execution capability needed to **prove** the authored SQL
parses against the real SHAW SIT Oracle instance.

Three CLI subcommands are exposed:

* ``bootstrap`` — applies the lookup/registry DDL + load files and the
  materialized expected-table DDL + refresh, in the order returned by
  :func:`_bootstrap_files_in_order`. Idempotent. (Shape B: the helper
  result sets are app_int-owned TABLES, not VIEWS; the app_int account
  lacks CREATE VIEW, ORA-01031.)
* ``query <name>`` — runs ``20_query/<name>.sql`` and prints the first
  N rows (default 10) so an operator can sanity-check the result shape.
* ``validate`` — runs every ``V_*`` and ``EXPECTED_*`` view with
  ``WHERE ROWNUM <= 1`` to confirm each view body actually parses on
  the live Oracle instance. The output is the canonical proof for
  "this view body is valid Oracle SQL against the SIT schema."

Statement allow-list (safety-critical)
--------------------------------------
Every SQL statement is classified before execution via
:func:`classify_statement`. The allow-list is **whitelist-only** AND
enforces **Policy A** — every harness-object reference must be
schema-qualified as ``app_int.<object>``:

* ``SELECT`` — anywhere (read-only by definition).
* ``CREATE TABLE app_int.LKP_VALDO_SHAW_*`` — harness-owned lookup tables.
* ``CREATE [OR REPLACE [FORCE]] VIEW app_int.V_SHAW_TRANERT_*`` — harness
  helper views.
* ``CREATE [OR REPLACE [FORCE]] VIEW app_int.EXPECTED_*`` — harness
  expected views.
* ``TRUNCATE TABLE app_int.LKP_VALDO_SHAW_*`` — harness lookup tables only.
* ``INSERT INTO app_int.LKP_VALDO_SHAW_*`` — harness lookup tables only.
* PL/SQL anonymous block ``BEGIN ... EXCEPTION WHEN OTHERS THEN IF SQLCODE
  != -955 THEN RAISE; END IF; END;`` — the ORA-00955-trapping idempotent-
  DDL pattern from ``010_lookup_tables.sql``. The embedded CREATE TABLE
  must itself be ``app_int.LKP_VALDO_SHAW_*``.

Anything else aborts the run with :class:`SmokeRunnerError`. In
particular UPDATE, DELETE, DROP, MERGE, ALTER, GRANT, REVOKE,
bare-identifier DDL (e.g. ``CREATE TABLE LKP_VALDO_SHAW_X`` without the
``app_int.`` prefix), DDL against a non-``app_int`` schema, or DDL/DML
against any app_int.* source-system table (``app_int.SHAW_LOAN_MASTER``,
``app_int.CONTACT``, etc.) is refused.

Note on bare identifiers: Oracle resolves an unqualified table name
against the connected user's default schema (``uzapp_ad0`` in our
deployment). Since harness objects live in ``app_int``, a bare reference
would either fail to find the object or — if a same-named object
happens to exist in ``uzapp_ad0`` — silently touch the wrong table.
The whitelist refuses bare identifiers to close that gap.

Audit logging
-------------
Every run writes a JSON-Lines audit file under
``reports/shaw_tranert_smoke/<UTC-ISO-timestamp>.jsonl``. One event per
classified statement, with:

* ``event``      — "statement_executed" | "statement_refused" | "row_sample"
* ``timestamp``  — UTC ISO 8601
* ``stmt_kind``  — classifier output
* ``stmt_target`` — table/view name when extractable, else null
* ``sql_first_60`` — first 60 chars of the SQL (no full body to keep
                     log files reviewable; full SQL is in the source files)
* ``rows_returned`` — for SELECTs, the row count
* ``error``      — Oracle error message when execution failed

The audit log is the paper trail of "what did the harness do against
SIT" for compliance review.

Credentials
-----------
Read via :class:`scripts.e2e_lib.secret_resolver.SecretResolver`:

* ``ORACLE_DSN_SIT``       — Oracle connection string (host:port/service).
* ``ORACLE_USER_SIT``      — Oracle username.
* ``ORACLE_PASSWORD_SIT``  — Oracle password (Jasypt-decrypted).

No values are ever logged. ``ORACLE_PASSWORD_SIT`` does not appear in
the audit log under any circumstance.

CLI
---
::

    python -m scripts.e2e_lib.shaw_tranert_smoke bootstrap
    python -m scripts.e2e_lib.shaw_tranert_smoke validate
    python -m scripts.e2e_lib.shaw_tranert_smoke query tranert_driver --limit 5
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Exception type
# --------------------------------------------------------------------------- #


class SmokeRunnerError(RuntimeError):
    """Raised for any smoke-runner refusal or misuse.

    Statement-classification failures, audit-log write failures, missing
    credentials, missing SQL files, and Oracle driver errors all surface
    via this single exception type. The error message NEVER contains
    secret values.
    """


# --------------------------------------------------------------------------- #
# Statement classification — the safety-critical layer
# --------------------------------------------------------------------------- #


#: Names of the harness-owned objects the smoke runner is allowed to
#: create, truncate, or insert into. Pattern matching is case-insensitive
#: but stored uppercase for stable comparisons.
_HARNESS_TABLE_PREFIX = "LKP_VALDO_SHAW_"
_HARNESS_HELPER_VIEW_PREFIX = "V_SHAW_TRANERT_"
_HARNESS_EXPECTED_VIEW_PREFIX = "EXPECTED_"

#: Materialized expected-result tables (shape (B), session 7). Because the
#: app_int account lacks the CREATE VIEW system privilege (ORA-01031 on every
#: CREATE VIEW — confirmed via session_privs), the L2b expected result sets
#: are materialized as app_int-owned tables via CREATE TABLE ... AS SELECT
#: (CTAS) and refreshed each run with TRUNCATE + INSERT ... SELECT, rather
#: than as views. They share the EXPECTED_ prefix with the (now unused)
#: expected-view prefix and are conventionally suffixed ``_TBL``.
_HARNESS_EXPECTED_TABLE_PREFIX = "EXPECTED_"

#: The Oracle schema all harness-owned objects must live in. Policy A
#: per session-6 decision: every CREATE/TRUNCATE/INSERT/CREATE VIEW
#: against a harness object must be schema-qualified ``app_int.<object>``.
#: Bare identifiers are refused even when the object name itself starts
#: with a harness prefix, because Oracle resolves bare names against the
#: connected user's default schema (``uzapp_ad0`` in our deployment),
#: which is not where harness objects live.
_HARNESS_SCHEMA = "APP_INT"

#: Materialized helper tables (shape (B), session 7). Formerly the
#: V_SHAW_TRANERT_* helper views; now CTAS tables for the same reason as
#: the expected tables (app_int lacks CREATE VIEW). They keep their
#: original V_SHAW_TRANERT_* names to minimize SQL churn — the leading
#: ``V_`` no longer denotes a view, only the harness helper role.
_HARNESS_HELPER_TABLE_PREFIX = "V_SHAW_TRANERT_"

#: Cross-source TRANERT lookup/dimension tables (session 7). These are
#: source-agnostic (not SHAW-specific): e.g. the source registry mapping
#: (LOCATION_CODE, ACTG_SYS_ID) -> SOURCE_SYSTEM + charge-off status. They
#: use the generalized ``LKP_VALDO_TRANERT_`` prefix to distinguish them
#: from the SHAW-specific property-derived lookups (``LKP_VALDO_SHAW_``).
_HARNESS_TRANERT_LOOKUP_PREFIX = "LKP_VALDO_TRANERT_"

#: Object-name prefixes the harness is allowed to CREATE TABLE (plain or
#: CTAS) and INSERT INTO. SHAW lookups carry source-specific static
#: reference data; TRANERT lookups carry cross-source dimension data;
#: helper tables carry the materialized building-block result sets;
#: expected tables carry the materialized per-record-type result sets
#: composed from the helpers.
_HARNESS_TABLE_PREFIXES = (
    _HARNESS_TABLE_PREFIX,
    _HARNESS_TRANERT_LOOKUP_PREFIX,
    _HARNESS_HELPER_TABLE_PREFIX,
    _HARNESS_EXPECTED_TABLE_PREFIX,
)

#: Explicit TRUNCATE allow-list (session-7 decision, option (a)).
#:
#: TRUNCATE is the only destructive verb the harness permits, so it is
#: gated by an explicit, reviewable set of fully-qualified table names
#: rather than by prefix alone. A statement that is schema-qualified and
#: prefix-valid but whose name is NOT in this set is still refused. This
#: is strictly tighter than a prefix check and gives reviewers a single
#: authoritative list of what the harness may ever empty.
#:
#: The set is kept in sync with the tables actually declared in the
#: bootstrap SQL by ``test_truncate_allowlist_matches_bootstrap_sql`` in
#: ``tests/unit/test_e2e_shaw_tranert_smoke.py`` — CI fails if they drift.
#:
#: Names are stored UPPER-CASE and fully schema-qualified to match the
#: classifier's ``APP_INT.<OBJECT>`` target format.
_TRUNCATE_ALLOWLIST: "frozenset[str]" = frozenset(
    {
        # Cross-source TRANERT dimension tables
        # (00_bootstrap/040_source_registry.sql).
        "APP_INT.LKP_VALDO_TRANERT_SOURCE_REGISTRY",
        # Static lookup tables (00_bootstrap/010_lookup_tables.sql).
        "APP_INT.LKP_VALDO_SHAW_REP_TYPE_ORI_BY_PRIN_SCH",
        "APP_INT.LKP_VALDO_SHAW_REP_TYP_URI_BY_TERMS_FREQ",
        "APP_INT.LKP_VALDO_SHAW_LOAN_MASTER_STATE_CD_BY_BK",
        "APP_INT.LKP_VALDO_SHAW_LEDGER_CD_BY_DEPT",
        "APP_INT.LKP_VALDO_SHAW_ACT_TYP_UI",
        "APP_INT.LKP_VALDO_SHAW_ORG_LEVEL4_BY_SAP_CENTER5",
        "APP_INT.LKP_VALDO_SHAW_CIF_ACT_CODE_BY_NAME_RELATION",
        # Materialized helper tables (00_bootstrap/030_expected_tables.sql),
        # formerly the V_SHAW_TRANERT_* helper views.
        "APP_INT.V_SHAW_TRANERT_LOAN_MASTER_BATCH_DATES",
        "APP_INT.V_SHAW_TRANERT_DRIVER",
        "APP_INT.V_SHAW_TRANERT_COST1",
        "APP_INT.V_SHAW_TRANERT_COST_MERGED",
        "APP_INT.V_SHAW_TRANERT_CBRS_ACCOUNT_SUMMARY",
        "APP_INT.V_SHAW_TRANERT_LOANS_NAME",
        "APP_INT.V_SHAW_TRANERT_CONTACTS_MERGED",
        "APP_INT.V_SHAW_TRANERT_BK1",
        "APP_INT.V_SHAW_TRANERT_BK3",
        "APP_INT.V_SHAW_TRANERT_STATE_PROVINCE",
        # Per-record-type expected tables (00_bootstrap/030_expected_tables.sql
        # + 10_load/020_refresh_expected.sql). Commit 5b1 adds the simple
        # types (the one_per_driver_row mappers + the batch header); commit
        # 5b2a adds the per-contact EXPECTED_32005_TBL (many_per_driver_row);
        # commit 5b2b adds the remaining complex types (EXPECTED_32010_TBL,
        # zero_or_one_per_driver_row, and EXPECTED_32025_TBL,
        # one_per_driver_row), completing the per-record-type set. The
        # EXPECTED_ table
        # prefix is already accepted by the classifier; only this explicit
        # TRUNCATE allow-list gates them.
        "APP_INT.EXPECTED_BATCH_HEADER_TBL",
        "APP_INT.EXPECTED_32000_TBL",
        "APP_INT.EXPECTED_32005_TBL",
        "APP_INT.EXPECTED_32010_TBL",
        "APP_INT.EXPECTED_32025_TBL",
        "APP_INT.EXPECTED_32040_TBL",
        "APP_INT.EXPECTED_32075_TBL",
    }
)


@dataclass(frozen=True)
class StatementClassification:
    """Result of classifying one SQL statement.

    Attributes:
        kind: One of ``"select"``, ``"create_table"``, ``"create_view"``,
            ``"truncate_table"``, ``"insert"``, ``"plsql_block"``.
        target: Table or view name when extractable (uppercase); ``None``
            for ``"select"`` and ``"plsql_block"`` whose targets vary.
        is_safe: ``True`` when the statement is allowed by the
            whitelist; ``False`` when it must be refused. When ``False``,
            ``reason`` describes why.
        reason: Human-readable explanation when ``is_safe`` is ``False``.
            Empty string when safe.
    """

    kind: str
    target: Optional[str]
    is_safe: bool
    reason: str = ""


def _strip_leading_comments(sql: str) -> str:
    """Return ``sql`` with leading ``--`` and ``/* */`` comments removed.

    The classifier needs to see the first real keyword. A statement may
    legitimately begin with explanatory comments (as every file in
    ``00_bootstrap/`` does).
    """
    pos = 0
    n = len(sql)
    while pos < n:
        # Skip whitespace.
        while pos < n and sql[pos].isspace():
            pos += 1
        if pos >= n:
            break
        # Line comment.
        if sql.startswith("--", pos):
            newline = sql.find("\n", pos)
            if newline == -1:
                return ""
            pos = newline + 1
            continue
        # Block comment.
        if sql.startswith("/*", pos):
            end = sql.find("*/", pos + 2)
            if end == -1:
                return ""
            pos = end + 2
            continue
        break
    return sql[pos:]


def _extract_qualified_target(
    stripped_sql: str, pattern: re.Pattern
) -> Optional[Tuple[Optional[str], str]]:
    """Apply ``pattern`` and return ``(schema, object)`` both uppercased.

    The pattern is expected to contain exactly two optional named
    groups: ``schema`` (the qualifier before the dot, or absent) and
    ``object`` (the table/view name itself, required).

    Returns ``None`` when the pattern does not match.
    Returns ``(None, "OBJ")`` for a bare identifier.
    Returns ``("SCHEMA", "OBJ")`` for a schema-qualified identifier.
    """
    m = pattern.search(stripped_sql)
    if not m:
        return None
    schema = m.group("schema")
    obj = m.group("object")
    return (schema.upper() if schema else None, obj.upper())


def _validate_harness_target(
    schema: Optional[str],
    obj: str,
    *,
    allowed_prefixes: Iterable[str],
    statement_kind: str,
) -> Tuple[bool, str]:
    """Validate a ``(schema, object)`` pair against harness Policy A.

    Policy A: harness objects must be schema-qualified as
    ``app_int.<object>``. Bare identifiers are refused even when the
    object name starts with an allowed prefix.

    Args:
        schema: Schema part from the parsed identifier (uppercased), or
            ``None`` if the identifier was bare.
        obj: Object name (uppercased).
        allowed_prefixes: One or more allowed prefixes for the object
            name (uppercased). Object must start with at least one of them.
        statement_kind: Human-readable statement kind for error messages
            (e.g. ``"CREATE TABLE"``).

    Returns:
        ``(is_safe, reason)`` — ``reason`` is empty when safe.
    """
    if schema is None:
        return (
            False,
            (
                f"{statement_kind} target {obj!r} is not schema-qualified; "
                f"Policy A requires {_HARNESS_SCHEMA.lower()}.{obj} "
                f"(bare identifiers resolve to the connected user's "
                f"default schema, which is not where harness objects live)"
            ),
        )
    if schema != _HARNESS_SCHEMA:
        return (
            False,
            (
                f"{statement_kind} target schema {schema!r} is not the "
                f"harness schema {_HARNESS_SCHEMA!r}"
            ),
        )
    if not any(obj.startswith(prefix) for prefix in allowed_prefixes):
        prefix_list = ", ".join(repr(p) for p in allowed_prefixes)
        return (
            False,
            (
                f"{statement_kind} target {obj!r} does not start with any "
                f"allowed harness prefix ({prefix_list})"
            ),
        )
    return (True, "")


#: Identifier pattern: optional ``schema.`` followed by required object
#: name. Both groups are named for unambiguous extraction. The schema
#: group is None when the identifier is bare.
_IDENT = r"(?:(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<object>[A-Za-z_][A-Za-z0-9_]*)"

_RE_SELECT = re.compile(r"^\s*SELECT\b", re.IGNORECASE)
_RE_CREATE_TABLE = re.compile(rf"^\s*CREATE\s+TABLE\s+{_IDENT}\b", re.IGNORECASE)
#: Detects the ``AS SELECT`` tail of a CREATE TABLE ... AS SELECT (CTAS)
#: statement so the classifier can report a distinct ``kind`` for it. The
#: ``AS`` may be followed by whitespace then ``(`` (parenthesised query) or
#: directly by ``SELECT`` / ``WITH``.
_RE_CTAS = re.compile(r"\bAS\s*\(?\s*(?:SELECT|WITH)\b", re.IGNORECASE)
_RE_CREATE_VIEW = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:FORCE\s+)?VIEW\s+{_IDENT}\b",
    re.IGNORECASE,
)
_RE_TRUNCATE = re.compile(rf"^\s*TRUNCATE\s+TABLE\s+{_IDENT}\b", re.IGNORECASE)
_RE_INSERT = re.compile(rf"^\s*INSERT\s+INTO\s+{_IDENT}\b", re.IGNORECASE)
_RE_PLSQL_BLOCK = re.compile(r"^\s*BEGIN\b", re.IGNORECASE)
_RE_PLSQL_EMBEDDED_CREATE_TABLE = re.compile(
    rf"CREATE\s+TABLE\s+{_IDENT}\b", re.IGNORECASE
)

#: Detects ``:bind_name`` bind-variable references in a SQL body. Oracle
#: refuses CREATE VIEW statements that reference bind variables
#: ("ORA-01027: bind variables not allowed for data definition
#: operations"), so the classifier refuses such statements at the gate
#: rather than letting them fail at execute() time. The regex matches a
#: colon NOT preceded by another colon (so PL/SQL ``::TYPE`` casts are
#: ignored) and followed by an identifier start character. Comments are
#: stripped before this regex runs (see :func:`_strip_all_comments`),
#: so a ``:name`` that appears only inside a ``--`` or ``/* */`` comment
#: does NOT trigger a refusal — matching Oracle's actual parse behaviour.
_RE_BIND_VARIABLE = re.compile(r"(?<!:):[A-Za-z_][A-Za-z0-9_]*")


def _strip_all_comments(sql: str) -> str:
    """Return ``sql`` with every ``--`` line comment and ``/* */`` block
    comment removed.

    Oracle strips comments before parsing the statement body, so any
    classifier check that mimics Oracle's parse behaviour (e.g. the
    bind-variable detector) must do the same. Unlike
    :func:`_strip_leading_comments` (which stops at the first non-comment
    character), this stripper scans the entire body.

    Single-quoted string literals are honoured: a ``--`` or ``/*`` inside
    ``'...'`` is part of the string and is NOT treated as a comment. A
    doubled single quote ``''`` inside a string represents a literal
    apostrophe per Oracle's rules.
    """
    out = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # Single-quoted string literal — copy verbatim including any
        # doubled-quote escapes, do not interpret comments inside.
        if ch == "'":
            out.append(ch)
            i += 1
            while i < n:
                if sql[i] == "'":
                    out.append("'")
                    i += 1
                    if i < n and sql[i] == "'":
                        # Doubled quote = escaped apostrophe inside the
                        # string; keep both and continue.
                        out.append("'")
                        i += 1
                        continue
                    break
                out.append(sql[i])
                i += 1
            continue
        # Line comment.
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            newline = sql.find("\n", i)
            if newline == -1:
                break
            # Preserve the newline so line numbers stay aligned.
            out.append("\n")
            i = newline + 1
            continue
        # Block comment.
        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _contains_bind_variable(sql: str) -> Optional[str]:
    """Return the first bind variable found in ``sql``, or ``None``.

    Comments are stripped before the bind-variable regex runs so a
    ``:name`` that appears only inside a comment does not trigger a
    refusal. Used by the CREATE VIEW classifier branch to refuse
    ``CREATE VIEW`` statements that would fail at execute() time with
    ORA-01027.
    """
    stripped = _strip_all_comments(sql)
    m = _RE_BIND_VARIABLE.search(stripped)
    return m.group(0) if m else None


def classify_statement(sql: str) -> StatementClassification:
    """Classify a SQL statement against the harness allow-list.

    Args:
        sql: The raw statement text. Leading comments are stripped before
            classification.

    Returns:
        A :class:`StatementClassification`. Callers must check
        ``is_safe`` before executing.
    """
    body = _strip_leading_comments(sql)
    if not body.strip():
        return StatementClassification(
            kind="empty",
            target=None,
            is_safe=False,
            reason="statement contains only comments / whitespace",
        )

    # SELECT — always allowed.
    if _RE_SELECT.match(body):
        return StatementClassification(kind="select", target=None, is_safe=True)

    # CREATE TABLE app_int.{LKP_VALDO_SHAW_*,EXPECTED_*} — allowed. This
    # covers both plain CREATE TABLE (lookups) and CREATE TABLE ... AS
    # SELECT (CTAS) used to materialize the expected-result tables. The
    # trailing AS SELECT body is read-only by definition, so CTAS adds no
    # write risk beyond the table creation itself. The ``kind`` reported
    # is ``create_table_as_select`` for CTAS so the audit log distinguishes
    # the two forms.
    parsed = _extract_qualified_target(body, _RE_CREATE_TABLE)
    if parsed is not None:
        schema, obj = parsed
        full = f"{schema}.{obj}" if schema else obj
        is_safe, reason = _validate_harness_target(
            schema,
            obj,
            allowed_prefixes=_HARNESS_TABLE_PREFIXES,
            statement_kind="CREATE TABLE",
        )
        is_ctas = _RE_CTAS.search(body) is not None
        return StatementClassification(
            kind="create_table_as_select" if is_ctas else "create_table",
            target=full,
            is_safe=is_safe,
            reason=reason,
        )

    # CREATE [OR REPLACE [FORCE]] VIEW app_int.{V_SHAW_TRANERT_*,EXPECTED_*} — allowed.
    parsed = _extract_qualified_target(body, _RE_CREATE_VIEW)
    if parsed is not None:
        schema, obj = parsed
        full = f"{schema}.{obj}" if schema else obj
        is_safe, reason = _validate_harness_target(
            schema,
            obj,
            allowed_prefixes=(
                _HARNESS_HELPER_VIEW_PREFIX,
                _HARNESS_EXPECTED_VIEW_PREFIX,
            ),
            statement_kind="CREATE VIEW",
        )
        # Defence against ORA-01027: Oracle refuses CREATE VIEW with
        # bind variables. The classifier catches this at the gate so
        # the failure surfaces as a refusal (with a clear reason) rather
        # than as an opaque Oracle error mid-bootstrap.
        if is_safe:
            bind = _contains_bind_variable(body)
            if bind is not None:
                is_safe = False
                reason = (
                    f"CREATE VIEW {full!r} body contains bind variable {bind!r}; "
                    f"Oracle refuses bind references in DDL (ORA-01027). "
                    f"Inline the binds as SQL expressions, or publish the "
                    f"parameterised form as a standalone .sql query rather "
                    f"than a view."
                )
        return StatementClassification(
            kind="create_view",
            target=full,
            is_safe=is_safe,
            reason=reason,
        )

    # TRUNCATE TABLE app_int.<allow-listed> — allowed. TRUNCATE is the only
    # destructive verb the harness permits, so beyond Policy-A schema
    # qualification and prefix validation it must additionally appear in
    # the explicit _TRUNCATE_ALLOWLIST. A prefix-valid name that is not in
    # the allow-list is refused — this is strictly tighter than a prefix
    # check and bounds exactly which tables the harness may ever empty.
    parsed = _extract_qualified_target(body, _RE_TRUNCATE)
    if parsed is not None:
        schema, obj = parsed
        full = f"{schema}.{obj}" if schema else obj
        is_safe, reason = _validate_harness_target(
            schema,
            obj,
            allowed_prefixes=_HARNESS_TABLE_PREFIXES,
            statement_kind="TRUNCATE TABLE",
        )
        if is_safe and full.upper() not in _TRUNCATE_ALLOWLIST:
            is_safe = False
            reason = (
                f"TRUNCATE TABLE target {full!r} is not in the TRUNCATE "
                f"allow-list. Only explicitly allow-listed harness tables "
                f"may be truncated; add the table to _TRUNCATE_ALLOWLIST "
                f"(and its bootstrap CREATE) if this is intended."
            )
        return StatementClassification(
            kind="truncate_table",
            target=full,
            is_safe=is_safe,
            reason=reason,
        )

    # INSERT INTO app_int.{LKP_VALDO_SHAW_*,EXPECTED_*} — allowed. Lookups
    # take INSERT ... VALUES (static reference data); expected tables take
    # INSERT ... SELECT (refresh) from the harness/source tables.
    parsed = _extract_qualified_target(body, _RE_INSERT)
    if parsed is not None:
        schema, obj = parsed
        full = f"{schema}.{obj}" if schema else obj
        is_safe, reason = _validate_harness_target(
            schema,
            obj,
            allowed_prefixes=_HARNESS_TABLE_PREFIXES,
            statement_kind="INSERT INTO",
        )
        return StatementClassification(
            kind="insert",
            target=full,
            is_safe=is_safe,
            reason=reason,
        )

    # PL/SQL anonymous block. Allowed when the embedded EXECUTE IMMEDIATE
    # body (if any) classifies as a harness CREATE TABLE.
    if _RE_PLSQL_BLOCK.match(body):
        # The 010_lookup_tables.sql blocks wrap a CREATE TABLE inside
        # EXECUTE IMMEDIATE. Locate the embedded identifier and validate
        # it is schema-qualified app_int.LKP_VALDO_SHAW_*. If the block
        # contains no CREATE TABLE, we refuse (a generic BEGIN block
        # could do anything).
        m = _RE_PLSQL_EMBEDDED_CREATE_TABLE.search(body)
        if m:
            schema = m.group("schema").upper() if m.group("schema") else None
            obj = m.group("object").upper()
            full = f"{schema}.{obj}" if schema else obj
            is_safe, reason = _validate_harness_target(
                schema,
                obj,
                allowed_prefixes=_HARNESS_TABLE_PREFIXES,
                statement_kind="PL/SQL embedded CREATE TABLE",
            )
            return StatementClassification(
                kind="plsql_block",
                target=full,
                is_safe=is_safe,
                reason=reason,
            )
        return StatementClassification(
            kind="plsql_block",
            target=None,
            is_safe=False,
            reason=(
                "PL/SQL block contains no recognisable CREATE TABLE for a "
                "harness-prefixed object"
            ),
        )

    # Anything else — refuse.
    first_keyword = body.split(None, 1)[0].upper() if body.split() else "<empty>"
    return StatementClassification(
        kind="other",
        target=None,
        is_safe=False,
        reason=(
            f"statement starts with {first_keyword!r}; only SELECT / harness-"
            f"prefixed CREATE TABLE / CREATE VIEW / TRUNCATE / INSERT / "
            f"PL/SQL DDL block are allowed"
        ),
    )


# --------------------------------------------------------------------------- #
# Audit logging
# --------------------------------------------------------------------------- #


@dataclass
class AuditLog:
    """Append-only JSON-Lines audit log for one smoke-runner invocation."""

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Truncate / create.
        self.path.write_text("", encoding="utf-8")

    def emit(self, event: str, **fields: Any) -> None:
        """Append one JSON event to the audit log."""
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        record.update(fields)
        # Forbid accidental credential leakage: defence in depth.
        for key, val in list(record.items()):
            if "password" in key.lower() or "secret" in key.lower():
                record[key] = "<redacted>"
        line = json.dumps(record, ensure_ascii=True, default=str)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def _new_audit_log() -> AuditLog:
    """Create a timestamped audit-log file under ``reports/shaw_tranert_smoke/``."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return AuditLog(path=Path("reports/shaw_tranert_smoke") / f"{ts}.jsonl")


# --------------------------------------------------------------------------- #
# Connection
# --------------------------------------------------------------------------- #


def _connect_sit(*, as_app_int: bool = False) -> Any:
    """Open an Oracle connection to SIT via SecretResolver-provided creds.

    Two connection identities are supported:

    * Default (``as_app_int=False``): reads ``ORACLE_DSN_SIT`` /
      ``ORACLE_USER_SIT`` / ``ORACLE_PASSWORD_SIT`` — the ``uzapp_ad0``
      application account.
    * ``as_app_int=True``: reads ``ORACLE_USER_SIT_APP_INT`` /
      ``ORACLE_PASSWORD_SIT_APP_INT`` and, for the DSN, prefers
      ``ORACLE_DSN_SIT_APP_INT`` but falls back to ``ORACLE_DSN_SIT`` when
      the ``_APP_INT`` DSN is unset/blank (the host:port/service is the
      same SIT instance; only the login identity differs).

    The ``as_app_int`` path exists to resolve the cross-schema view
    privilege issue (#18): creating ``app_int``-owned views that read
    ``uzapp_ad0``-owned tables requires connecting as ``app_int`` (which
    owns the views) rather than as ``uzapp_ad0`` (which lacks
    ``SELECT WITH GRANT OPTION`` on the source tables).

    Args:
        as_app_int: When ``True``, connect as the ``app_int`` schema owner
            using the ``*_APP_INT`` credential variables.

    Returns:
        A python-oracledb connection in thin mode. Caller is responsible
        for closing it.

    Raises:
        SmokeRunnerError: If any required secret is missing or the
            connection fails. The exception message never contains the
            password.
    """
    try:
        import oracledb  # type: ignore[import-not-found]
    except ImportError as exc:
        raise SmokeRunnerError(
            "python-oracledb is not installed; "
            "run `pip install oracledb` to install it"
        ) from exc

    # Local import so unit tests can run without touching SecretResolver
    # at module-import time.
    from scripts.e2e_lib.secret_resolver import (  # noqa: WPS433
        SecretResolver,
        SecretResolverError,
    )

    resolver = SecretResolver.default()
    if as_app_int:
        user_var = "ORACLE_USER_SIT_APP_INT"
        password_var = "ORACLE_PASSWORD_SIT_APP_INT"
        try:
            user = resolver.get(user_var)
            password = resolver.get(password_var)
        except SecretResolverError as exc:
            raise SmokeRunnerError(
                f"missing app_int SIT credential: {exc}. Set "
                f"ORACLE_USER_SIT_APP_INT and ORACLE_PASSWORD_SIT_APP_INT "
                f"in your .env or environment (the DSN falls back to "
                f"ORACLE_DSN_SIT when ORACLE_DSN_SIT_APP_INT is unset)."
            ) from exc
        # DSN: prefer the app_int-specific value, fall back to the shared
        # SIT DSN. The host/service is identical; only the login differs.
        dsn = resolver.get_optional("ORACLE_DSN_SIT_APP_INT")
        if not dsn:
            try:
                dsn = resolver.get("ORACLE_DSN_SIT")
            except SecretResolverError as exc:
                raise SmokeRunnerError(
                    f"missing app_int SIT credential: {exc}. Set either "
                    f"ORACLE_DSN_SIT_APP_INT or ORACLE_DSN_SIT in your "
                    f".env or environment."
                ) from exc
    else:
        try:
            dsn = resolver.get("ORACLE_DSN_SIT")
            user = resolver.get("ORACLE_USER_SIT")
            password = resolver.get("ORACLE_PASSWORD_SIT")
        except SecretResolverError as exc:
            raise SmokeRunnerError(
                f"missing SIT credential: {exc}. Set ORACLE_DSN_SIT, "
                f"ORACLE_USER_SIT, ORACLE_PASSWORD_SIT in your .env or "
                f"environment."
            ) from exc

    try:
        return oracledb.connect(user=user, password=password, dsn=dsn)
    except Exception as exc:  # noqa: BLE001 — driver decides type
        # Re-wrap without ever including the password in the message.
        raise SmokeRunnerError(
            f"Oracle connection to SIT failed (user={user}, dsn={dsn}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc


# --------------------------------------------------------------------------- #
# Statement execution helpers
# --------------------------------------------------------------------------- #


_TRANERT_DIR = Path("config/e2e/sources/SHAW/sql/tranert")

#: The SHAW TRANERT reconciliation spec consumed by the L2b engine. Used by
#: the ``reconcile`` subcommand to drive a live diff and render an HTML report.
_RECONCILIATION_SPEC = Path("config/e2e/sources/SHAW/reconciliation/tranert.yml")

#: Directory the ``reconcile`` subcommand writes its HTML reports into.
_RECONCILE_REPORT_DIR = Path("reports/shaw_tranert_reconcile")


def _bootstrap_files_in_order() -> List[Path]:
    """Return the bootstrap+load SQL files in execution order.

    Order:
      1. Lookup-table DDL (idempotent CREATE via ORA-00955-trapping blocks).
      2. Source-registry DDL (idempotent CREATE) — the cross-source
         (LOCATION_CODE, ACTG_SYS_ID) -> SOURCE_SYSTEM dimension.
      3. Lookup-table load (TRUNCATE + INSERT ... VALUES).
      4. Source-registry seed (TRUNCATE + INSERT ... VALUES).
      5. Expected-table DDL: CREATE TABLE ... AS SELECT (CTAS), wrapped in
         the same idempotent ORA-00955-trapping block so a re-run does not
         fail on an already-created table. This both defines the table
         structure and seeds it the first time.
      6. Expected-table refresh: TRUNCATE + INSERT ... SELECT so each run
         recomputes the expected result sets from current source data.

    Shape (B), session 7: the expected result sets are materialized as
    app_int-owned tables (the app_int account lacks CREATE VIEW; ORA-01031).
    """
    return [
        _TRANERT_DIR / "00_bootstrap" / "010_lookup_tables.sql",
        _TRANERT_DIR / "00_bootstrap" / "040_source_registry.sql",
        _TRANERT_DIR / "10_load" / "010_load_lookups_from_csv.sql",
        _TRANERT_DIR / "10_load" / "030_refresh_source_registry.sql",
        _TRANERT_DIR / "00_bootstrap" / "030_expected_tables.sql",
        _TRANERT_DIR / "10_load" / "020_refresh_expected.sql",
    ]


def _query_file(name: str) -> Path:
    """Resolve a query name (e.g. ``"tranert_driver"``) to its SQL file path."""
    if "/" in name or "\\" in name or ".." in name:
        raise SmokeRunnerError(f"query name {name!r} contains illegal path chars")
    path = _TRANERT_DIR / "20_query" / f"{name}.sql"
    if not path.is_file():
        raise SmokeRunnerError(f"query file not found: {path}")
    return path


def _split_file(path: Path) -> List[str]:
    """Read a SQL file and split into individual statements via sql_bootstrap."""
    from scripts.e2e_lib.sql_bootstrap import _split_statements

    return _split_statements(path.read_text(encoding="utf-8"))


def _execute_safe(
    cursor: Any,
    sql: str,
    audit: AuditLog,
    *,
    source_file: str,
    sample_rows: Optional[int] = None,
) -> Optional[List[Tuple[Any, ...]]]:
    """Classify, refuse if unsafe, otherwise execute. Returns sampled rows for SELECTs.

    Args:
        cursor: An open PEP-249 cursor.
        sql: The statement text.
        audit: Audit-log writer.
        source_file: Repo-relative path the SQL came from (logged).
        sample_rows: For SELECTs only: number of rows to fetchmany().
            ``None`` means do not fetch (only validate the statement).

    Returns:
        ``None`` for non-SELECT statements, or the list of fetched rows
        for SELECT statements.

    Raises:
        SmokeRunnerError: If classification refuses the statement, or if
            Oracle raises during execution.
    """
    classification = classify_statement(sql)
    audit_kwargs: Dict[str, Any] = {
        "stmt_kind": classification.kind,
        "stmt_target": classification.target,
        "sql_first_60": sql.strip().replace("\n", " ")[:60],
        "source_file": source_file,
    }
    if not classification.is_safe:
        audit.emit("statement_refused", reason=classification.reason, **audit_kwargs)
        raise SmokeRunnerError(
            f"refused statement in {source_file}: {classification.reason}"
        )

    try:
        cursor.execute(sql)
    except Exception as exc:  # noqa: BLE001 — Oracle driver decides type
        audit.emit(
            "statement_failed",
            error=f"{type(exc).__name__}: {exc}",
            **audit_kwargs,
        )
        raise SmokeRunnerError(
            f"Oracle error executing statement in {source_file}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    rows: Optional[List[Tuple[Any, ...]]] = None
    if classification.kind == "select" and sample_rows:
        try:
            rows = cursor.fetchmany(sample_rows)
        except Exception as exc:  # noqa: BLE001
            audit.emit(
                "fetch_failed",
                error=f"{type(exc).__name__}: {exc}",
                **audit_kwargs,
            )
            raise SmokeRunnerError(
                f"Oracle fetch error after SELECT in {source_file}: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    audit.emit(
        "statement_executed",
        rows_returned=(len(rows) if rows is not None else None),
        **audit_kwargs,
    )
    return rows


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #


def run_bootstrap(audit: AuditLog, *, as_app_int: bool = False) -> None:
    """Apply the three bootstrap files in order against SIT.

    Args:
        audit: Audit-log writer.
        as_app_int: When ``True``, connect as the ``app_int`` schema owner
            (resolves the cross-schema view-privilege issue, #18).
    """
    audit.emit("connection_identity", as_app_int=as_app_int)
    conn = _connect_sit(as_app_int=as_app_int)
    try:
        cursor = conn.cursor()
        try:
            for sql_file in _bootstrap_files_in_order():
                if not sql_file.is_file():
                    raise SmokeRunnerError(f"bootstrap file not found: {sql_file}")
                audit.emit(
                    "file_starting",
                    source_file=str(sql_file).replace("\\", "/"),
                )
                statements = _split_file(sql_file)
                for stmt in statements:
                    _execute_safe(
                        cursor,
                        stmt,
                        audit,
                        source_file=str(sql_file).replace("\\", "/"),
                    )
                audit.emit(
                    "file_completed",
                    source_file=str(sql_file).replace("\\", "/"),
                    statements_executed=len(statements),
                )
            try:
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                raise SmokeRunnerError(
                    f"commit failed: {type(exc).__name__}: {exc}"
                ) from exc
            audit.emit("bootstrap_committed")
        finally:
            cursor.close()
    finally:
        conn.close()


def run_validate(audit: AuditLog, *, as_app_int: bool = False) -> Dict[str, str]:
    """SELECT 1 row from every expected table. Returns name -> 'ok' | error.

    The probe SELECTs are schema-qualified ``app_int.<table>`` to match the
    CTAS emission in ``030_expected_tables.sql``. Bare-identifier probes
    would resolve against the connected user's default schema
    (``uzapp_ad0``), which is not where harness objects live.

    Shape (B), session 7: the expected result sets are materialized tables
    (the app_int account lacks CREATE VIEW; ORA-01031), so the validator
    probes the CTAS table names rather than view names.

    Args:
        audit: Audit-log writer.
        as_app_int: When ``True``, connect as the ``app_int`` schema owner.
    """
    # Extract every CTAS table name from 030_expected_tables.sql. The
    # pattern captures the optional schema qualifier and the object name
    # separately so the result dict keys are unambiguous. The CREATE may
    # be a bare ``CREATE TABLE`` or appear inside an EXECUTE IMMEDIATE
    # idempotent-DDL block; both forms match this pattern.
    bootstrap_tables = _TRANERT_DIR / "00_bootstrap" / "030_expected_tables.sql"
    if not bootstrap_tables.is_file():
        raise SmokeRunnerError(f"expected-tables file not found: {bootstrap_tables}")
    text = bootstrap_tables.read_text(encoding="utf-8")
    view_decls = re.findall(
        r"CREATE\s+TABLE\s+" r"(?:([A-Za-z_][A-Za-z0-9_]*)\.)?([A-Za-z_][A-Za-z0-9_]*)",
        text,
        flags=re.IGNORECASE,
    )
    if not view_decls:
        raise SmokeRunnerError(f"no expected tables found in {bootstrap_tables}")

    results: Dict[str, str] = {}
    audit.emit("connection_identity", as_app_int=as_app_int)
    conn = _connect_sit(as_app_int=as_app_int)
    try:
        cursor = conn.cursor()
        try:
            for schema, obj in view_decls:
                # Schema-qualify the probe. When the view's CREATE statement
                # did not include a schema (which Policy A refuses anyway,
                # but the validator's regex is permissive for diagnostic
                # output), fall back to the configured harness schema so
                # the probe at least targets the right place.
                qualified = (
                    f"{schema}.{obj}" if schema else f"{_HARNESS_SCHEMA.lower()}.{obj}"
                )
                probe = f"SELECT * FROM {qualified} WHERE ROWNUM <= 1"
                try:
                    _execute_safe(
                        cursor,
                        probe,
                        audit,
                        source_file=f"<validate probe: {qualified}>",
                        sample_rows=1,
                    )
                    results[qualified.upper()] = "ok"
                except SmokeRunnerError as exc:
                    results[qualified.upper()] = str(exc)
        finally:
            cursor.close()
    finally:
        conn.close()
    return results


def run_discover(
    table_names: List[str], audit: AuditLog, *, as_app_int: bool = False
) -> Dict[str, List[Tuple[str, str]]]:
    """Look up which Oracle schemas own each of ``table_names``.

    For each requested name, queries ``ALL_OBJECTS`` for every (OWNER,
    OBJECT_TYPE) the connected user can see and returns the result.
    Pure read-only — uses a single SELECT with a bind-array, refused
    by no classifier rule.

    Args:
        table_names: Unqualified table/view names (case-insensitive).
        audit: Audit-log writer.

    Returns:
        Mapping of upper-cased requested name to a list of
        ``(owner, object_type)`` tuples. A missing name maps to an
        empty list so the caller can report "not found" explicitly.
    """
    if not table_names:
        return {}
    # Build a comma-separated list of placeholders for the IN clause.
    # python-oracledb supports named binds in lists via {name: value}.
    placeholders = ", ".join(f":n{i}" for i in range(len(table_names)))
    binds = {f"n{i}": name.upper() for i, name in enumerate(table_names)}
    probe = (
        f"SELECT OWNER, OBJECT_NAME, OBJECT_TYPE "
        f"FROM ALL_OBJECTS "
        f"WHERE OBJECT_NAME IN ({placeholders}) "
        f"AND OBJECT_TYPE IN ('TABLE', 'VIEW', 'SYNONYM') "
        f"ORDER BY OBJECT_NAME, OWNER"
    )
    results: Dict[str, List[Tuple[str, str]]] = {n.upper(): [] for n in table_names}
    audit.emit("connection_identity", as_app_int=as_app_int)
    conn = _connect_sit(as_app_int=as_app_int)
    try:
        cursor = conn.cursor()
        try:
            # Classifier accepts SELECTs unconditionally; record the
            # discovery in the audit log for traceability.
            classification = classify_statement(probe)
            audit.emit(
                "discover_query",
                stmt_kind=classification.kind,
                sql_first_60=probe[:60],
                table_names=table_names,
            )
            cursor.execute(probe, binds)
            for owner, object_name, object_type in cursor.fetchall():
                key = str(object_name).upper()
                if key in results:
                    results[key].append((str(owner), str(object_type)))
        finally:
            cursor.close()
    finally:
        conn.close()
    return results


def run_query(
    name: str, *, limit: int, audit: AuditLog, as_app_int: bool = False
) -> List[Tuple[Any, ...]]:
    """Run the named wrapper SELECT and return up to ``limit`` rows."""
    sql_path = _query_file(name)
    statements = _split_file(sql_path)
    if len(statements) != 1:
        raise SmokeRunnerError(
            f"query file {sql_path} must contain exactly one statement; "
            f"got {len(statements)}"
        )
    audit.emit("connection_identity", as_app_int=as_app_int)
    conn = _connect_sit(as_app_int=as_app_int)
    try:
        cursor = conn.cursor()
        try:
            rows = _execute_safe(
                cursor,
                statements[0],
                audit,
                source_file=str(sql_path).replace("\\", "/"),
                sample_rows=limit,
            )
            return rows or []
        finally:
            cursor.close()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Reconcile + HTML report
# --------------------------------------------------------------------------- #


def _html_escape(value: Any) -> str:
    """Escape a value for safe inclusion in HTML text/attribute content."""
    import html

    return html.escape("" if value is None else str(value))


def _render_reconcile_html(report: Any, file_path: Path, spec: Any, ts: str) -> str:
    """Render a :class:`ReconciliationReport` to a self-contained HTML page.

    Args:
        report: The ``ReconciliationReport`` returned by the L2b engine.
        file_path: The output file that was reconciled.
        spec: The loaded ``ReconciliationSpec`` (for source/file-type labels).
        ts: UTC timestamp string used in the title and filename.

    Returns:
        A complete, dependency-free HTML document as a string.
    """
    esc = _html_escape
    n_viol = len(report.violations)
    passed = n_viol == 0 and report.rows_unknown_type == 0
    status_txt = "PASS" if passed else "FAIL"
    status_col = "#1a7f37" if passed else "#cf222e"

    by_kind: Dict[str, int] = {}
    for v in report.violations:
        by_kind[v.kind] = by_kind.get(v.kind, 0) + 1
    kind_html = "".join(
        f"<li><b>{esc(k)}</b>: {esc(n)}</li>" for k, n in sorted(by_kind.items())
    ) or "<li>none</li>"

    pertype_rows = []
    for rt, c in sorted(report.per_type_counts.items()):
        pertype_rows.append(
            "<tr><td><code>{rt}</code></td><td>{fc}</td>"
            "<td>{ec}</td><td>{fm}</td></tr>".format(
                rt=esc(rt),
                fc=esc(c.file_rows),
                ec=esc(c.expected_rows),
                fm=esc(c.field_mismatches),
            )
        )
    pertype_html = "\n".join(pertype_rows) or "<tr><td colspan=4>(none)</td></tr>"

    viol_rows = []
    for v in report.violations:
        viol_rows.append(
            "<tr><td>{k}</td><td><code>{rt}</code></td><td>{key}</td>"
            "<td>{f}</td><td>{exp}</td><td>{act}</td><td>{ln}</td>"
            "<td>{msg}</td></tr>".format(
                k=esc(v.kind),
                rt=esc(v.record_type),
                key=esc(", ".join(v.key_values)),
                f=esc(v.field),
                exp=esc(v.expected),
                act=esc(v.actual),
                ln=esc(v.line_number),
                msg=esc(v.message),
            )
        )
    viol_html = "\n".join(viol_rows) or (
        "<tr><td colspan=8 style='text-align:center;color:#1a7f37'>"
        "No violations — clean reconcile.</td></tr>"
    )

    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>\n"
        f"<title>L2b SQL Reconciliation - {esc(file_path.name)}</title>\n"
        "<style>\n"
        " body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "margin:0;background:#f6f8fa;color:#1f2328}\n"
        " .wrap{max-width:1100px;margin:0 auto;padding:24px}\n"
        " h1{font-size:22px;margin:0 0 4px} .sub{color:#656d76;font-size:13px}\n"
        f" .banner{{display:inline-block;color:#fff;background:{status_col};"
        "font-weight:700;padding:6px 16px;border-radius:6px;margin:12px 0;"
        "font-size:18px}\n"
        " .grid{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0}\n"
        " .stat{background:#fff;border:1px solid #d0d7de;border-radius:8px;"
        "padding:12px 18px;min-width:120px}\n"
        " .stat .v{font-size:26px;font-weight:700} .stat .l{color:#656d76;"
        "font-size:12px}\n"
        " table{width:100%;border-collapse:collapse;background:#fff;"
        "border:1px solid #d0d7de;border-radius:8px;overflow:hidden;"
        "margin:8px 0 24px}\n"
        " th,td{text-align:left;padding:8px 10px;border-bottom:1px solid #eaeef2;"
        "font-size:13px;vertical-align:top}\n"
        " th{background:#f6f8fa;font-weight:600}\n"
        " code{background:#eff1f3;padding:1px 5px;border-radius:4px;font-size:12px}\n"
        " h2{font-size:16px;margin:20px 0 4px}\n"
        "</style></head><body><div class=wrap>\n"
        " <h1>L2b SQL Reconciliation Gate - live report</h1>\n"
        f" <div class=sub>File: <code>{esc(str(file_path))}</code> &middot; "
        f"Source: {esc(spec.source)} / {esc(spec.file_type)} &middot; "
        f"Generated: {esc(ts)} (UTC)</div>\n"
        f" <div class=banner>{status_txt} - {esc(n_viol)} violation(s)</div>\n"
        " <div class=grid>\n"
        f"   <div class=stat><div class=v>{esc(report.rows_compared)}</div>"
        "<div class=l>rows compared</div></div>\n"
        f"   <div class=stat><div class=v>{esc(report.rows_unknown_type)}</div>"
        "<div class=l>unknown record type</div></div>\n"
        f"   <div class=stat><div class=v>{esc(n_viol)}</div>"
        "<div class=l>violations</div></div>\n"
        " </div>\n"
        f" <h2>Violations by kind</h2><ul>{kind_html}</ul>\n"
        " <h2>Per-record-type counts</h2>\n"
        " <table><thead><tr><th>Record type</th><th>File rows</th>"
        "<th>Expected rows</th><th>Field mismatches</th></tr></thead>\n"
        f"   <tbody>{pertype_html}</tbody></table>\n"
        f" <h2>Violations ({esc(n_viol)})</h2>\n"
        " <table><thead><tr><th>Kind</th><th>Record type</th><th>Key</th>"
        "<th>Field</th><th>Expected</th><th>Actual</th><th>Line</th>"
        "<th>Message</th></tr></thead>\n"
        f"   <tbody>{viol_html}</tbody></table>\n"
        " <div class=sub>Engine: db_truth_comparator.reconcile() &middot; "
        "strict trimmed-string equality.</div>\n"
        "</div></body></html>\n"
    )


def _render_reconcile_error_html(
    error_message: str, file_path: Path, spec: Any, ts: str
) -> str:
    """Render an HTML report for a reconcile that aborted before producing a report.

    Used when the engine raises (e.g. a fixed-width parse failure or an
    unknown record-type discriminator under the umbrella's ``default_action:
    error``). The gate still emits a report documenting the FAIL rather than
    dying with no artifact.

    Args:
        error_message: The engine/parse error text.
        file_path: The output file that was being reconciled.
        spec: The loaded ``ReconciliationSpec`` (for source/file-type labels).
        ts: UTC timestamp string.

    Returns:
        A complete, dependency-free HTML document as a string.
    """
    esc = _html_escape
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>\n"
        f"<title>L2b SQL Reconciliation - {esc(file_path.name)}</title>\n"
        "<style>\n"
        " body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;"
        "margin:0;background:#f6f8fa;color:#1f2328}\n"
        " .wrap{max-width:1100px;margin:0 auto;padding:24px}\n"
        " h1{font-size:22px;margin:0 0 4px} .sub{color:#656d76;font-size:13px}\n"
        " .banner{display:inline-block;color:#fff;background:#cf222e;"
        "font-weight:700;padding:6px 16px;border-radius:6px;margin:12px 0;"
        "font-size:18px}\n"
        " .err{background:#fff;border:1px solid #d0d7de;border-left:4px solid #cf222e;"
        "border-radius:8px;padding:14px 16px;margin:8px 0;"
        "font-family:ui-monospace,Consolas,monospace;font-size:13px;"
        "white-space:pre-wrap;word-break:break-word}\n"
        " h2{font-size:16px;margin:20px 0 4px}\n"
        "</style></head><body><div class=wrap>\n"
        " <h1>L2b SQL Reconciliation Gate - live report</h1>\n"
        f" <div class=sub>File: <code>{esc(str(file_path))}</code> &middot; "
        f"Source: {esc(spec.source)} / {esc(spec.file_type)} &middot; "
        f"Generated: {esc(ts)} (UTC)</div>\n"
        " <div class=banner>FAIL - reconcile aborted</div>\n"
        " <h2>Error</h2>\n"
        f" <div class=err>{esc(error_message)}</div>\n"
        " <div class=sub>The reconcile could not complete. This is typically a "
        "fixed-width layout/parse problem (a shifted field changed the "
        "record-type discriminator, or a line width changed), not a field-value "
        "drift. Verify the file layout against the per-record-type mapping "
        "before re-running.</div>\n"
        "</div></body></html>\n"
    )


def run_reconcile(
    file_path: Path,
    *,
    audit: AuditLog,
    as_app_int: bool = False,
    spec_path: Path = _RECONCILIATION_SPEC,
    report_dir: Path = _RECONCILE_REPORT_DIR,
    skip_bootstrap: bool = False,
) -> Tuple[Any, Path]:
    """Run the L2b reconciliation live against ``file_path`` and write an HTML report.

    Loads the reconciliation spec, opens a SIT connection, drives
    :func:`scripts.e2e_lib.db_truth_comparator.reconcile` (which bootstraps
    and refreshes the expected tables unless ``skip_bootstrap=True``), then
    renders the report to ``report_dir``.

    Args:
        file_path: The Valdo output file to reconcile.
        audit: Audit-log writer.
        as_app_int: When ``True``, connect as the ``app_int`` schema owner.
        spec_path: Reconciliation spec YAML (defaults to the SHAW TRANERT spec).
        report_dir: Directory the HTML report is written into.
        skip_bootstrap: Forwarded to the engine; skips bootstrap+load when
            ``True`` (useful when the expected tables are already fresh).

    Returns:
        A ``(report, html_path)`` tuple. ``report`` is the
        ``ReconciliationReport`` on success, or ``None`` when the engine
        aborted (e.g. a fixed-width parse / unknown-discriminator failure); in
        that case an error report is still written to ``html_path``.

    Raises:
        SmokeRunnerError: If the file or spec is missing/invalid, or the SIT
            connection fails. Engine reconcile failures do NOT raise — they
            produce an error report and a ``None`` report.
    """
    # Local imports so unit tests and the other subcommands need neither the
    # engine nor the spec loader at module-import time.
    from scripts.e2e_lib.db_truth_comparator import (
        DbTruthComparatorError,
        reconcile,
    )
    from scripts.e2e_lib.reconciliation_spec import (
        ReconciliationSpecError,
        load_spec,
    )

    if not file_path.is_file():
        raise SmokeRunnerError(f"reconcile file not found: {file_path}")
    if not spec_path.is_file():
        raise SmokeRunnerError(f"reconciliation spec not found: {spec_path}")

    try:
        spec = load_spec(spec_path)
    except ReconciliationSpecError as exc:
        raise SmokeRunnerError(f"invalid reconciliation spec: {exc}") from exc

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit.emit(
        "reconcile_started",
        file=str(file_path).replace("\\", "/"),
        spec=str(spec_path).replace("\\", "/"),
        as_app_int=as_app_int,
        skip_bootstrap=skip_bootstrap,
    )

    report: Any = None
    error_message: Optional[str] = None
    conn = _connect_sit(as_app_int=as_app_int)
    try:
        report = reconcile(conn, spec, file_path, skip_bootstrap=skip_bootstrap)
    except DbTruthComparatorError as exc:
        # An engine/parse failure (e.g. a shifted fixed-width field changed the
        # record-type discriminator) is a gate FAIL, not a crash: capture it and
        # still emit a report.
        error_message = str(exc)
    finally:
        conn.close()

    report_dir.mkdir(parents=True, exist_ok=True)
    out_path = report_dir / f"reconcile_{file_path.stem}_{ts}.html"
    if report is not None:
        html_doc = _render_reconcile_html(report, file_path, spec, ts)
        audit.emit(
            "reconcile_completed",
            rows_compared=report.rows_compared,
            rows_unknown_type=report.rows_unknown_type,
            violations=len(report.violations),
            report=str(out_path).replace("\\", "/"),
        )
    else:
        html_doc = _render_reconcile_error_html(
            error_message or "unknown error", file_path, spec, ts
        )
        audit.emit(
            "reconcile_aborted",
            error=error_message,
            report=str(out_path).replace("\\", "/"),
        )
    out_path.write_text(html_doc, encoding="utf-8")
    return report, out_path


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point. Returns 0 on success, 2 on smoke-runner refusal."""
    parser = argparse.ArgumentParser(
        prog="shaw_tranert_smoke",
        description=(
            "Read-only smoke runner for SHAW TRANERT SQL artifacts against SIT."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Set the logger to INFO (default WARNING).",
    )
    parser.add_argument(
        "--as-app_int",
        action="store_true",
        help=(
            "Connect as the app_int schema owner using "
            "ORACLE_USER_SIT_APP_INT / ORACLE_PASSWORD_SIT_APP_INT "
            "(DSN falls back to ORACLE_DSN_SIT). Required for creating "
            "app_int-owned views that read uzapp_ad0-owned tables."
        ),
    )

    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("bootstrap", help="Apply lookup-table DDL, load DML, and views.")
    sub.add_parser("validate", help="SELECT 1 from every harness view.")
    q = sub.add_parser("query", help="Run a 20_query/<name>.sql wrapper.")
    q.add_argument("name", help='Wrapper name (e.g. "tranert_driver").')
    q.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max rows to fetch and print (default 10).",
    )
    rc = sub.add_parser(
        "reconcile",
        help="Reconcile an output file against the SQL truth and write an HTML report.",
    )
    rc.add_argument("file", help="Path to the Valdo output file to reconcile.")
    rc.add_argument(
        "--skip-bootstrap",
        action="store_true",
        help="Skip bootstrap+load (use when the expected tables are already fresh).",
    )
    d = sub.add_parser(
        "discover",
        help="Look up which schemas own a list of tables/views in SIT.",
    )
    d.add_argument(
        "names",
        nargs="+",
        help="One or more unqualified table/view names (case-insensitive).",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    audit = _new_audit_log()
    as_app_int = args.as_app_int
    try:
        if args.cmd == "bootstrap":
            run_bootstrap(audit, as_app_int=as_app_int)
            sys.stdout.write(f"bootstrap complete. audit: {audit.path}\n")
            return 0
        if args.cmd == "validate":
            results = run_validate(audit, as_app_int=as_app_int)
            ok = sum(1 for v in results.values() if v == "ok")
            fail = len(results) - ok
            sys.stdout.write(
                f"validate complete: {ok} ok, {fail} failed. " f"audit: {audit.path}\n"
            )
            for view, status in sorted(results.items()):
                marker = "[OK]  " if status == "ok" else "[FAIL]"
                sys.stdout.write(f"  {marker} {view}\n")
                if status != "ok":
                    sys.stdout.write(f"         {status}\n")
            return 0 if fail == 0 else 2
        if args.cmd == "query":
            rows = run_query(
                args.name, limit=args.limit, audit=audit, as_app_int=as_app_int
            )
            sys.stdout.write(
                f"{args.name}: {len(rows)} row(s) (limit={args.limit}). "
                f"audit: {audit.path}\n"
            )
            for row in rows:
                sys.stdout.write(f"  {row}\n")
            return 0
        if args.cmd == "reconcile":
            report, out_path = run_reconcile(
                Path(args.file),
                audit=audit,
                as_app_int=as_app_int,
                skip_bootstrap=args.skip_bootstrap,
            )
            if report is None:
                sys.stdout.write(
                    "reconcile aborted: file could not be reconciled "
                    "(parse/layout failure) -> FAIL\n"
                )
                sys.stdout.write(f"  report: {out_path}\n")
                sys.stdout.write(f"  audit:  {audit.path}\n")
                return 2
            n_viol = len(report.violations)
            passed = n_viol == 0 and report.rows_unknown_type == 0
            sys.stdout.write(
                f"reconcile complete: {report.rows_compared} rows compared, "
                f"{report.rows_unknown_type} unknown-type, {n_viol} violation(s) "
                f"-> {'PASS' if passed else 'FAIL'}\n"
            )
            sys.stdout.write(f"  report: {out_path}\n")
            sys.stdout.write(f"  audit:  {audit.path}\n")
            return 0 if passed else 2
        if args.cmd == "discover":
            mapping = run_discover(args.names, audit, as_app_int=as_app_int)
            sys.stdout.write(f"discover complete. audit: {audit.path}\n")
            for name in args.names:
                key = name.upper()
                locations = mapping.get(key, [])
                if not locations:
                    sys.stdout.write(f"  [MISSING] {name}\n")
                else:
                    for owner, otype in locations:
                        sys.stdout.write(f"  [{otype:7}] {owner}.{name.upper()}\n")
            return 0
        # argparse(required=True) makes this unreachable.
        sys.stderr.write(f"unknown subcommand: {args.cmd!r}\n")
        return 2
    except SmokeRunnerError as exc:
        sys.stderr.write(f"error: {exc}\n")
        sys.stderr.write(f"audit: {audit.path}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
