"""Unit tests for ``scripts.e2e_lib.sql_bootstrap``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, call

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.sql_bootstrap import (  # noqa: E402
    SqlBootstrapError,
    SqlBootstrapResult,
    SqlFileResult,
    run_sql_directory,
)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_conn() -> MagicMock:
    """Build a MagicMock that mimics a PEP-249 connection.

    Each call to ``conn.cursor()`` returns the same cursor mock so tests can
    inspect the full ``execute`` call sequence in order. Real PEP-249
    drivers typically return a fresh cursor per call, but for verifying
    statement ordering and arguments a single shared cursor mock is
    simpler and equivalent.
    """
    conn = MagicMock(name="conn")
    cursor = MagicMock(name="cursor")
    conn.cursor.return_value = cursor
    return conn


def _executed_statements(conn: MagicMock) -> List[str]:
    """Extract the SQL strings passed to ``cursor.execute`` in call order."""
    cursor = conn.cursor.return_value
    return [c.args[0] for c in cursor.execute.call_args_list]


# --------------------------------------------------------------------------- #
# Empty / missing directory
# --------------------------------------------------------------------------- #


class TestDirectoryHandling:
    """Behaviour around the directory argument."""

    def test_empty_directory_returns_zeroed_result(self, tmp_path: Path) -> None:
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result == SqlBootstrapResult(
            files_run=0, statements_executed=0, files=()
        )
        conn.cursor.assert_not_called()

    def test_directory_with_non_sql_files_only_is_treated_as_empty(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "README.md").write_text("not sql")
        (tmp_path / "notes.txt").write_text("also not sql")
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result.files_run == 0
        assert result.statements_executed == 0
        conn.cursor.assert_not_called()

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with pytest.raises(SqlBootstrapError, match="sql directory not found"):
            run_sql_directory(_make_conn(), missing)

    def test_file_instead_of_directory_raises(self, tmp_path: Path) -> None:
        not_a_dir = tmp_path / "a.sql"
        not_a_dir.write_text("SELECT 1 FROM dual;\n")
        with pytest.raises(SqlBootstrapError, match="sql directory not found"):
            run_sql_directory(_make_conn(), not_a_dir)


# --------------------------------------------------------------------------- #
# Statement splitting
# --------------------------------------------------------------------------- #


class TestStatementSplitting:
    """The ``;`` / ``/`` terminator rules."""

    def test_single_statement_without_terminator(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text("SELECT 1 FROM dual\n")
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result.statements_executed == 1
        assert _executed_statements(conn) == ["SELECT 1 FROM dual"]

    def test_single_statement_with_trailing_semicolon_on_same_line(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "10.sql").write_text("SELECT 1 FROM dual;\n")
        conn = _make_conn()
        run_sql_directory(conn, tmp_path)
        # Trailing ';' on the final line is stripped so the cursor sees a
        # clean statement (Oracle does not require it).
        assert _executed_statements(conn) == ["SELECT 1 FROM dual"]

    def test_three_statements_split_on_semicolon_lines(self, tmp_path: Path) -> None:
        sql = (
            "CREATE OR REPLACE VIEW v_a AS SELECT 1 FROM dual\n"
            ";\n"
            "CREATE OR REPLACE VIEW v_b AS SELECT 2 FROM dual\n"
            ";\n"
            "CREATE OR REPLACE VIEW v_c AS SELECT 3 FROM dual\n"
            ";\n"
        )
        (tmp_path / "10.sql").write_text(sql)
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result.statements_executed == 3
        executed = _executed_statements(conn)
        assert executed[0].startswith("CREATE OR REPLACE VIEW v_a")
        assert executed[1].startswith("CREATE OR REPLACE VIEW v_b")
        assert executed[2].startswith("CREATE OR REPLACE VIEW v_c")

    def test_semicolon_inside_line_does_not_split(self, tmp_path: Path) -> None:
        # The ';' inside the VALUES literal is part of the statement; only a
        # ';' alone on its own line terminates.
        sql = "INSERT INTO t (a, b) VALUES ('a;b', 1)\n;\n"
        (tmp_path / "10.sql").write_text(sql)
        conn = _make_conn()
        run_sql_directory(conn, tmp_path)
        executed = _executed_statements(conn)
        assert len(executed) == 1
        assert "'a;b'" in executed[0]

    def test_plsql_block_terminated_by_slash(self, tmp_path: Path) -> None:
        sql = (
            "CREATE OR REPLACE PROCEDURE p AS\n" "BEGIN\n" "    NULL;\n" "END;\n" "/\n"
        )
        (tmp_path / "10.sql").write_text(sql)
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result.statements_executed == 1
        executed = _executed_statements(conn)
        assert "BEGIN" in executed[0]
        assert "END;" in executed[0]
        # The terminator itself is not part of the executed statement.
        assert not executed[0].rstrip().endswith("/")

    def test_mixed_semicolon_and_slash_terminators(self, tmp_path: Path) -> None:
        sql = (
            "CREATE OR REPLACE VIEW v_x AS SELECT 1 FROM dual\n"
            ";\n"
            "CREATE OR REPLACE PROCEDURE p AS\n"
            "BEGIN\n"
            "    NULL;\n"
            "END;\n"
            "/\n"
            "INSERT INTO t VALUES (1)\n"
            ";\n"
        )
        (tmp_path / "10.sql").write_text(sql)
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result.statements_executed == 3
        executed = _executed_statements(conn)
        assert executed[0].startswith("CREATE OR REPLACE VIEW v_x")
        assert executed[1].startswith("CREATE OR REPLACE PROCEDURE p")
        assert executed[2].startswith("INSERT INTO t")

    def test_empty_sql_file_is_a_no_op(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text("")
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result.files_run == 1
        assert result.statements_executed == 0
        assert result.files == (
            SqlFileResult(file_name="10.sql", statements_executed=0),
        )

    def test_whitespace_only_sql_file_is_a_no_op(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text("\n\n   \n\n")
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert result.statements_executed == 0


# --------------------------------------------------------------------------- #
# File ordering and parameter binding
# --------------------------------------------------------------------------- #


class TestFileOrderingAndBinding:
    """Sorted iteration order and parameter passthrough."""

    def test_files_run_in_sorted_filename_order(self, tmp_path: Path) -> None:
        # Lexicographic sort: "10_b.sql" < "20_a.sql".
        (tmp_path / "20_a.sql").write_text("SELECT 'twenty-a' FROM dual;\n")
        (tmp_path / "10_b.sql").write_text("SELECT 'ten-b' FROM dual;\n")
        (tmp_path / "30_c.sql").write_text("SELECT 'thirty-c' FROM dual;\n")
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        assert [f.file_name for f in result.files] == [
            "10_b.sql",
            "20_a.sql",
            "30_c.sql",
        ]
        executed = _executed_statements(conn)
        assert "ten-b" in executed[0]
        assert "twenty-a" in executed[1]
        assert "thirty-c" in executed[2]

    def test_params_are_passed_to_execute(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text("SELECT :schema FROM dual;\n")
        conn = _make_conn()
        run_sql_directory(conn, tmp_path, params={"schema": "AUDIT"})
        cursor = conn.cursor.return_value
        cursor.execute.assert_called_once_with(
            "SELECT :schema FROM dual", {"schema": "AUDIT"}
        )

    def test_no_params_defaults_to_empty_dict(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text("SELECT 1 FROM dual;\n")
        conn = _make_conn()
        run_sql_directory(conn, tmp_path)
        cursor = conn.cursor.return_value
        # The function passes an empty dict (not None) to keep PEP-249
        # drivers from interpreting the missing arg as a positional binding.
        cursor.execute.assert_called_once_with("SELECT 1 FROM dual", {})


# --------------------------------------------------------------------------- #
# Failure surfacing
# --------------------------------------------------------------------------- #


class TestFailureSurfacing:
    """Mid-run failures abort with diagnostic context."""

    def test_statement_failure_raises_with_file_and_index(self, tmp_path: Path) -> None:
        sql = (
            "SELECT 1 FROM dual\n"
            ";\n"
            "SELECT 2 FROM dual\n"
            ";\n"
            "SELECT 3 FROM dual\n"
            ";\n"
        )
        (tmp_path / "20_load.sql").write_text(sql)
        conn = _make_conn()
        cursor = conn.cursor.return_value
        # Fail on the second statement.
        cursor.execute.side_effect = [None, RuntimeError("ORA-00942"), None]
        with pytest.raises(
            SqlBootstrapError, match=r"20_load\.sql.*statement 2"
        ) as exc_info:
            run_sql_directory(conn, tmp_path)
        # The original driver exception is chained as __cause__.
        assert isinstance(exc_info.value.__cause__, RuntimeError)
        assert "ORA-00942" in str(exc_info.value.__cause__)

    def test_failure_in_first_file_stops_subsequent_files(self, tmp_path: Path) -> None:
        (tmp_path / "10_first.sql").write_text("BAD SQL HERE;\n")
        (tmp_path / "20_second.sql").write_text("SELECT 1 FROM dual;\n")
        conn = _make_conn()
        cursor = conn.cursor.return_value
        cursor.execute.side_effect = RuntimeError("syntax error")
        with pytest.raises(SqlBootstrapError, match=r"10_first\.sql"):
            run_sql_directory(conn, tmp_path)
        # Second file's statement was never attempted.
        assert cursor.execute.call_count == 1


# --------------------------------------------------------------------------- #
# Connection / transaction policy
# --------------------------------------------------------------------------- #


class TestConnectionPolicy:
    """The function does not commit; it only executes."""

    def test_connection_is_not_committed(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text("SELECT 1 FROM dual;\n")
        conn = _make_conn()
        run_sql_directory(conn, tmp_path)
        conn.commit.assert_not_called()
        conn.rollback.assert_not_called()

    def test_connection_is_not_committed_on_failure(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text("BAD;\n")
        conn = _make_conn()
        conn.cursor.return_value.execute.side_effect = RuntimeError("boom")
        with pytest.raises(SqlBootstrapError):
            run_sql_directory(conn, tmp_path)
        conn.commit.assert_not_called()


# --------------------------------------------------------------------------- #
# Value-type surface
# --------------------------------------------------------------------------- #


class TestResultTypes:
    """The frozen value types behave correctly."""

    def test_results_are_frozen(self) -> None:
        result = SqlBootstrapResult(files_run=0, statements_executed=0, files=())
        with pytest.raises((AttributeError, TypeError)):
            result.files_run = 99  # type: ignore[misc]
        file_result = SqlFileResult(file_name="x.sql", statements_executed=1)
        with pytest.raises((AttributeError, TypeError)):
            file_result.statements_executed = 99  # type: ignore[misc]

    def test_per_file_counts_match_summary(self, tmp_path: Path) -> None:
        (tmp_path / "10.sql").write_text(
            "SELECT 1 FROM dual;\nSELECT 2 FROM dual;\n".replace(
                "SELECT 2", ";\nSELECT 2"
            )
        )
        (tmp_path / "20.sql").write_text("SELECT 'two-twenty' FROM dual;\n")
        conn = _make_conn()
        result = run_sql_directory(conn, tmp_path)
        # Per-file sum equals the total.
        assert (
            sum(f.statements_executed for f in result.files)
            == result.statements_executed
        )
        assert result.files_run == len(result.files)
