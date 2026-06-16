"""Round-trip escaping + stable-numeric tests for ``extract_to_file`` (S16-2, #426).

Before S16-2 every ``extract_to_file`` path joined values with the delimiter and
a bare ``str(val)``, so a value containing the delimiter (``A|B``) or a newline
silently corrupted the flat file: the delimiter split into a spurious extra
column and the newline split into a spurious extra row.  These tests prove the
fix — the stdlib ``csv`` module (``csv.writer`` with ``QUOTE_MINIMAL``) — writes
output that round-trips through the **same reader the comparator uses**
(:func:`pandas.read_csv` with ``sep=delimiter``), with no corruption and no
spurious quoting of values that don't need it.

They cover the shared writer helper, all three adapters (SQLite executed against
a real in-``tmp_path`` DB; Oracle/PostgreSQL via the shared helper with a fake
cursor since no real server is available in unit CI), and the
:class:`~src.database.extractor.DataExtractor` orchestration path.

The NUMERIC case asserts the extract-side rendering is *stable* — a value like
``100.50`` written and read back is a single, consistent token rather than a
float-trailing-zero render that makes db-compare flag a spurious diff.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pandas as pd
import pytest

from src.database.adapters._delimited_writer import _format_value, _write_delimited
from src.database.adapters.sqlite_adapter import SQLiteAdapter
from src.database.extractor import DataExtractor


DELIM = "|"


def _read_back(path, delimiter: str = DELIM) -> pd.DataFrame:
    """Read a delimited extract exactly the way the comparator read path does.

    Mirrors ``src/services/compare_service.py`` / the pipe-delimited parser:
    ``pd.read_csv(sep=delimiter, dtype=str, keep_default_na=False)``.  pandas
    applies standard CSV quoting on read (``quotechar='"'``, ``QUOTE_MINIMAL``),
    so it is symmetric with the ``csv.writer`` write path under test.
    """
    return pd.read_csv(
        str(path), sep=delimiter, dtype=str, keep_default_na=False, header=0
    )


# ---------------------------------------------------------------------------
# Shared helper — the single source of truth all adapters route through
# ---------------------------------------------------------------------------

class TestWriteDelimitedHelper:
    def test_delimiter_in_value_round_trips(self, tmp_path) -> None:
        """A value containing the delimiter is quoted and reads back intact."""
        out = tmp_path / "delim.txt"
        n = _write_delimited(
            str(out),
            DELIM,
            ["ID", "NOTE"],
            [[(1, "A|B"), (2, "plain")]],
        )
        assert n == 2
        df = _read_back(out)
        # No spurious extra column from the embedded delimiter.
        assert list(df.columns) == ["ID", "NOTE"]
        assert df.iloc[0]["NOTE"] == "A|B"
        assert df.iloc[1]["NOTE"] == "plain"

    def test_newline_in_value_round_trips(self, tmp_path) -> None:
        """A value containing a newline is quoted and reads back as one row."""
        out = tmp_path / "nl.txt"
        n = _write_delimited(
            str(out),
            DELIM,
            ["ID", "NOTE"],
            [[(1, "line1\nline2"), (2, "single")]],
        )
        assert n == 2
        df = _read_back(out)
        # No spurious extra row from the embedded newline.
        assert len(df) == 2
        assert df.iloc[0]["NOTE"] == "line1\nline2"
        assert df.iloc[1]["NOTE"] == "single"

    def test_quote_in_value_round_trips(self, tmp_path) -> None:
        """A value containing the quote char is escaped and reads back intact."""
        out = tmp_path / "q.txt"
        _write_delimited(str(out), DELIM, ["V"], [[('say "hi"',)]])
        df = _read_back(out)
        assert df.iloc[0]["V"] == 'say "hi"'

    def test_simple_values_not_quoted(self, tmp_path) -> None:
        """QUOTE_MINIMAL: values needing no quoting are written bare."""
        out = tmp_path / "simple.txt"
        _write_delimited(
            str(out), DELIM, ["ID", "NAME"], [[(1, "alice"), (2, "bob")]]
        )
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "ID|NAME"
        assert lines[1] == "1|alice"
        assert lines[2] == "2|bob"
        # No stray quote characters anywhere.
        assert '"' not in out.read_text(encoding="utf-8")

    def test_none_renders_empty(self, tmp_path) -> None:
        """NULL/None values render as an empty field (unchanged contract)."""
        out = tmp_path / "none.txt"
        _write_delimited(str(out), DELIM, ["A", "B"], [[(None, "x")]])
        df = _read_back(out)
        assert df.iloc[0]["A"] == ""
        assert df.iloc[0]["B"] == "x"

    def test_returns_total_across_batches(self, tmp_path) -> None:
        """Row count sums across multiple fetched batches."""
        out = tmp_path / "batches.txt"
        n = _write_delimited(
            str(out), DELIM, ["N"], [[(1,), (2,)], [(3,)], []]
        )
        assert n == 3


# ---------------------------------------------------------------------------
# NUMERIC stability — no float trailing-zero render that fakes a db-compare diff
# ---------------------------------------------------------------------------

class TestNumericFormatting:
    def test_decimal_trailing_zero_normalized(self) -> None:
        """``Decimal('100.50')`` renders the same stable token as float 100.5."""
        # The PG smoke saw Decimal('100.50') (one engine) vs float 100.5 (other)
        # flagged as a spurious diff. Stable extract-side rendering collapses
        # both to a single representation.
        assert _format_value(Decimal("100.50")) == _format_value(100.5)

    def test_decimal_integral_no_dot(self) -> None:
        """An integral Decimal renders as a clean integer token, no scale tail.

        ``Decimal('100.00')`` -> ``'100'`` (not ``'100.00'`` or ``'1E+2'``):
        a stable, scale-free representation for a whole number. Residual note:
        a *float* ``100.0`` still renders as ``'100.0'`` (Python's ``str``);
        collapsing ``'100'`` == ``'100.0'`` across backends is a compare-time
        normalisation concern, intentionally out of scope for the extract side.
        """
        assert _format_value(Decimal("100.00")) == "100"

    def test_float_round_trips_as_string(self, tmp_path) -> None:
        """A float value reads back as a single stable token (one column)."""
        out = tmp_path / "num.txt"
        _write_delimited(str(out), DELIM, ["AMT"], [[(100.50,)]])
        df = _read_back(out)
        assert len(df.columns) == 1
        # Stable: 100.50 -> '100.5', a single consistent token.
        assert df.iloc[0]["AMT"] == "100.5"

    def test_plain_int_unchanged(self) -> None:
        assert _format_value(42) == "42"

    def test_string_unchanged(self) -> None:
        assert _format_value("hello") == "hello"


# ---------------------------------------------------------------------------
# SQLite adapter — real DB, full round-trip through the comparator reader
# ---------------------------------------------------------------------------

class TestSQLiteAdapterEscaping:
    @pytest.fixture()
    def seeded_db(self, tmp_path):
        db_path = tmp_path / "esc.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE T (ID INTEGER, NOTE TEXT)")
        conn.executemany(
            "INSERT INTO T VALUES (?, ?)",
            [(1, "A|B"), (2, "line1\nline2"), (3, "plain")],
        )
        conn.commit()
        conn.close()
        return str(db_path)

    def test_round_trip_delimiter_and_newline(self, seeded_db, tmp_path) -> None:
        adapter = SQLiteAdapter(db_path=seeded_db)
        adapter.connect()
        out = tmp_path / "sqlite_esc.txt"
        n = adapter.extract_to_file("SELECT ID, NOTE FROM T", str(out))
        adapter.disconnect()

        assert n == 3
        df = _read_back(out)
        assert list(df.columns) == ["ID", "NOTE"]
        assert len(df) == 3
        notes = {df.iloc[i]["NOTE"] for i in range(3)}
        assert notes == {"A|B", "line1\nline2", "plain"}

    def test_simple_values_unchanged(self, seeded_db, tmp_path) -> None:
        adapter = SQLiteAdapter(db_path=seeded_db)
        adapter.connect()
        out = tmp_path / "sqlite_simple.txt"
        adapter.extract_to_file(
            "SELECT ID FROM T WHERE NOTE = 'plain'", str(out)
        )
        adapter.disconnect()
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines == ["ID", "3"]


# ---------------------------------------------------------------------------
# Oracle / PostgreSQL adapters — route through the shared helper with a fake
# cursor (no real server in unit CI). Proves the chunked fetch path escapes.
# ---------------------------------------------------------------------------

class _FakeCursor:
    """Minimal cursor honouring execute/description/fetchmany/close."""

    def __init__(self, col_names, rows, chunk=2):
        self.description = [(c,) for c in col_names]
        self._rows = list(rows)
        self._chunk = chunk
        self._pos = 0

    def execute(self, query, params=None):
        return None

    def fetchmany(self, size):
        batch = self._rows[self._pos : self._pos + self._chunk]
        self._pos += len(batch)
        return batch

    def close(self):
        return None


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor


class TestOracleAdapterEscaping:
    def test_round_trip_delimiter_and_newline(self, tmp_path) -> None:
        from src.database.adapters.oracle_adapter import OracleAdapter

        adapter = OracleAdapter.__new__(OracleAdapter)
        adapter._connection = _FakeConn(
            _FakeCursor(["ID", "NOTE"], [(1, "A|B"), (2, "x\ny"), (3, "ok")])
        )
        out = tmp_path / "oracle_esc.txt"
        n = adapter.extract_to_file("SELECT ID, NOTE FROM T", str(out))

        assert n == 3
        df = _read_back(out)
        assert list(df.columns) == ["ID", "NOTE"]
        assert len(df) == 3
        assert {df.iloc[i]["NOTE"] for i in range(3)} == {"A|B", "x\ny", "ok"}


class TestPostgreSQLAdapterEscaping:
    def test_round_trip_delimiter_and_newline(self, tmp_path) -> None:
        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        adapter = PostgreSQLAdapter.__new__(PostgreSQLAdapter)
        adapter._connection = _FakeConn(
            _FakeCursor(["ID", "NOTE"], [(1, "A|B"), (2, "x\ny"), (3, "ok")])
        )
        out = tmp_path / "pg_esc.txt"
        n = adapter.extract_to_file("SELECT ID, NOTE FROM T", str(out))

        assert n == 3
        df = _read_back(out)
        assert list(df.columns) == ["ID", "NOTE"]
        assert len(df) == 3
        assert {df.iloc[i]["NOTE"] for i in range(3)} == {"A|B", "x\ny", "ok"}


# ---------------------------------------------------------------------------
# DataExtractor orchestration path — extract_to_file end-to-end on SQLite
# ---------------------------------------------------------------------------

class TestExtractorPathEscaping:
    @pytest.fixture()
    def seeded_db(self, tmp_path):
        db_path = tmp_path / "ext.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE T (ID INTEGER, NOTE TEXT)")
        conn.executemany(
            "INSERT INTO T VALUES (?, ?)", [(1, "A|B"), (2, "n1\nn2")]
        )
        conn.commit()
        conn.close()
        return str(db_path)

    def test_extractor_round_trips_through_reader(self, seeded_db, tmp_path) -> None:
        adapter = SQLiteAdapter(db_path=seeded_db)
        adapter.connect()
        ext = DataExtractor(adapter)
        out = tmp_path / "ext_out.txt"
        stats = ext.extract_to_file(table_name="T", output_file=str(out))
        adapter.disconnect()

        assert stats["total_rows"] == 2
        df = _read_back(out)
        assert list(df.columns) == ["ID", "NOTE"]
        assert {df.iloc[i]["NOTE"] for i in range(2)} == {"A|B", "n1\nn2"}
