"""SQL bootstrap: execute a directory of ``.sql`` files against an Oracle DB.

Purpose
-------
The L2b SQL-Truth gate (issue #17) reconciles a Valdo output file against a
SQL truth source derived from the same input data. Producing that truth source
requires running a set of versioned ``.sql`` files in deterministic order:

* ``bootstrap_dir`` — ``CREATE OR REPLACE`` views, ``CREATE TABLE IF NOT
  EXISTS`` helper tables, sequences. Runs first on every harness invocation.
* ``load_dir`` — ``MERGE`` from lookup CSVs into helper tables. Runs second.
* ``query_dir`` — the per-record-type ``expected_*.sql`` views and any
  driver queries. The L2b comparator (commit 3 of #17) issues SELECTs
  against these views; this module is responsible for ensuring they exist.

This module provides one public function, :func:`run_sql_directory`, that
iterates a directory's ``.sql`` files in sorted filename order, splits each
file into statements, and executes them via a PEP-249 connection.

Idempotency is a **convention**, not enforced. Callers should write SQL using
``CREATE OR REPLACE``, ``CREATE … IF NOT EXISTS``, ``MERGE``, and
``TRUNCATE`` so that re-running the bootstrap is safe; this module does not
inspect statements to confirm that property.

Statement splitting
-------------------
Two terminators are recognised, matching SQL*Plus conventions:

* A line that, after stripping whitespace, contains only ``;`` ends a plain
  SQL statement. A ``;`` *inside* a line (e.g. inside a string literal
  ``VALUES ('a;b', 1)``) does not split — the ``;`` must be the sole
  non-whitespace character on its own line.
* A line that starts with ``/`` in column 1 and contains nothing else after
  stripping whitespace ends a PL/SQL block. The ``/`` itself is discarded;
  the block body is sent to the cursor as one statement. This is the Oracle
  convention for terminating ``CREATE OR REPLACE PROCEDURE … END;`` blocks
  whose embedded semicolons would otherwise be misinterpreted.

Comments (``-- …`` line comments and ``/* … */`` block comments) are passed
through to the cursor verbatim; Oracle's own parser handles them.

Connection / transaction policy
-------------------------------
The PEP-249 connection is **not** committed by this function. Per-file and
per-directory transactionality is the caller's responsibility (the L2b
comparator commits once per gate run). The connection's auto-commit setting,
if any, is respected.

Public API
----------
* :class:`SqlFileResult`        — per-file execution summary, frozen.
* :class:`SqlBootstrapResult`   — directory-level summary, frozen.
* :class:`SqlBootstrapError`    — single exception type.
* :func:`run_sql_directory`     — directory → :class:`SqlBootstrapResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Mapping, Optional, Tuple

# --------------------------------------------------------------------------- #
# Exception type
# --------------------------------------------------------------------------- #


class SqlBootstrapError(ValueError):
    """Raised for any SQL bootstrap failure.

    Single exception type matches the house style of
    :mod:`scripts.e2e_lib.path_resolver` and
    :mod:`scripts.e2e_lib.reconciliation_spec`. Callers can match on this
    one class for every failure mode (missing directory, statement
    execution failure, malformed SQL file).
    """


# --------------------------------------------------------------------------- #
# Value types (frozen)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SqlFileResult:
    """Per-file execution summary.

    Attributes:
        file_name: Filename relative to the bootstrap directory (no path).
        statements_executed: Number of statements run from this file.
    """

    file_name: str
    statements_executed: int


@dataclass(frozen=True)
class SqlBootstrapResult:
    """Directory-level execution summary.

    Attributes:
        files_run: Number of ``.sql`` files processed.
        statements_executed: Total statements run across all files.
        files: Per-file results in execution order.
    """

    files_run: int
    statements_executed: int
    files: Tuple[SqlFileResult, ...]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def run_sql_directory(
    conn: Any,
    directory: Path,
    *,
    params: Optional[Mapping[str, str]] = None,
) -> SqlBootstrapResult:
    """Run every ``.sql`` file in ``directory`` against ``conn``.

    Files are processed in sorted filename order (``00_*.sql`` before
    ``10_*.sql``). Each file is split into statements per the rules in the
    module docstring; each statement is executed via
    ``conn.cursor().execute(stmt, params or {})``.

    The connection is **not** committed by this function.

    Args:
        conn: A PEP-249 connection (``python-oracledb`` connection, or any
            compatible mock for testing). The function calls ``conn.cursor()``
            and ``cursor.execute(stmt, params)`` only.
        directory: Filesystem path to the directory containing ``.sql`` files.
            An empty or non-existent directory is permitted only when the
            directory exists and contains zero ``.sql`` files; a missing
            directory raises :class:`SqlBootstrapError`.
        params: Optional mapping of named-parameter substitutions. Passed
            through verbatim to ``cursor.execute`` for every statement.
            ``None`` is treated as ``{}``.

    Returns:
        A :class:`SqlBootstrapResult` summarising what was executed.

    Raises:
        SqlBootstrapError: If ``directory`` does not exist, if a ``.sql``
            file cannot be read, or if any statement fails. The error
            message names the offending file and 1-indexed statement
            position.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise SqlBootstrapError(
            f"sql directory not found or not a directory: {directory}"
        )

    sql_files = sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix == ".sql"
    )
    bind_params: Mapping[str, str] = params or {}

    per_file: List[SqlFileResult] = []
    total_statements = 0

    for sql_file in sql_files:
        try:
            text = sql_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise SqlBootstrapError(
                f"could not read sql file {sql_file}: {exc}"
            ) from exc

        statements = _split_statements(text)

        executed = 0
        for stmt_index, stmt in enumerate(statements, start=1):
            cursor = conn.cursor()
            try:
                cursor.execute(stmt, bind_params)
            except Exception as exc:  # noqa: BLE001 — caller's DB driver decides type
                raise SqlBootstrapError(
                    f"sql execution failed in {sql_file.name}, "
                    f"statement {stmt_index}: {exc}"
                ) from exc
            executed += 1

        per_file.append(
            SqlFileResult(file_name=sql_file.name, statements_executed=executed)
        )
        total_statements += executed

    return SqlBootstrapResult(
        files_run=len(sql_files),
        statements_executed=total_statements,
        files=tuple(per_file),
    )


# --------------------------------------------------------------------------- #
# Internal: statement splitter
# --------------------------------------------------------------------------- #


def _split_statements(text: str) -> List[str]:
    """Split SQL text into individual statements.

    Recognises two terminators:

    * A line whose only non-whitespace content is ``;`` ends a plain SQL
      statement. The ``;`` is discarded.
    * A line that begins with ``/`` in column 1 and whose only non-whitespace
      content is ``/`` ends a PL/SQL block. The ``/`` is discarded.

    Statements are stripped of leading/trailing whitespace. Statements that
    are empty after stripping (e.g. a trailing terminator with no following
    SQL) are not emitted.

    Args:
        text: The raw contents of a ``.sql`` file.

    Returns:
        Ordered list of statement strings, one per executable statement.
    """
    statements: List[str] = []
    buffer: List[str] = []

    for line in text.splitlines():
        stripped = line.strip()

        # PL/SQL block terminator: '/' alone in column 1 of its own line.
        if line.startswith("/") and stripped == "/":
            candidate = "\n".join(buffer).strip()
            if candidate:
                statements.append(candidate)
            buffer = []
            continue

        # Plain SQL terminator: ';' alone on its own line (whitespace ok).
        if stripped == ";":
            candidate = "\n".join(buffer).strip()
            if candidate:
                statements.append(candidate)
            buffer = []
            continue

        buffer.append(line)

    # Flush any trailing statement that did not have an explicit terminator.
    tail = "\n".join(buffer).strip()
    if tail:
        # If the tail ends with a semicolon at the end of its last line
        # (e.g. a single-statement file written as "SELECT 1 FROM dual;"),
        # strip the trailing semicolon so the cursor sees a clean statement.
        if tail.endswith(";"):
            tail = tail[:-1].rstrip()
        if tail:
            statements.append(tail)

    return statements
