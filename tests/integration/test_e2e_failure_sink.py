"""Integration test for ``scripts.e2e_lib.failure_sink.FailureSink``.

Uses an in-memory SQLite database as a stand-in for Oracle. The
SQLite DDL below mirrors the columns of ``AUDIT.VALDO_RUN_FAILURES``
declared in ``scripts/sql/audit_valdo_run_failures.sql`` — same names,
same nullability, same defaults — but uses SQLite-compatible types and
``?`` placeholders (``paramstyle="qmark"``).

These tests cover gap (a) from ``prompts/e2e_batch_testing_prompt.md``:
* one row per failed gate
* append-only behavior (no UPDATE / DELETE via the public API)
* idempotency expectations (duplicates are accepted by design — see the
  docstring on ``FailureSink``)
* JSON serialization of ``failure_detail``
* atomic batch insert on error
* default-timestamp behavior when ``failed_at`` is omitted
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.failure_sink import (  # noqa: E402
    DEFAULT_TABLE,
    FailureRecord,
    FailureSink,
    FailureSinkError,
)


# SQLite-compatible mirror of scripts/sql/audit_valdo_run_failures.sql.
# Table name omits the schema prefix because SQLite has no schemas; tests
# pass table="VALDO_RUN_FAILURES" to the sink to match.
SQLITE_DDL = """
CREATE TABLE VALDO_RUN_FAILURES (
    failure_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT     NOT NULL,
    env               TEXT     NOT NULL,
    source            TEXT     NOT NULL,
    pipeline_name     TEXT     NOT NULL,
    gate_name         TEXT     NOT NULL,
    gate_stage        TEXT,
    layer             TEXT,
    file_name         TEXT,
    file_type         TEXT,
    mapping_path      TEXT,
    mapping_version   TEXT,
    error_type        TEXT     NOT NULL,
    error_count       INTEGER  DEFAULT 0,
    row_count         INTEGER,
    report_path       TEXT,
    blocking          INTEGER  DEFAULT 1,
    failed_at         TEXT     DEFAULT CURRENT_TIMESTAMP,
    failure_detail    TEXT
);
"""


@pytest.fixture()
def sqlite_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(SQLITE_DDL)
    yield conn
    conn.close()


@pytest.fixture()
def sink(sqlite_conn):
    return FailureSink.from_connection(
        sqlite_conn, table="VALDO_RUN_FAILURES", paramstyle="qmark"
    )


def _base_record(**overrides) -> FailureRecord:
    """Build a valid baseline record; override fields as needed per-test."""
    defaults = dict(
        run_id="20260513_120000",
        env="sit",
        source="SRC_A",
        pipeline_name="SRC_A.pipeline",
        gate_name="L1_structural",
        gate_stage="output",
        layer="L1",
        file_name="SRC_A_P327_20260513.txt",
        file_type="P327",
        mapping_path="config/mappings/SRC_A_P327.json",
        mapping_version="1.4.0",
        error_type="structural",
        error_count=3,
        row_count=10_482,
        report_path="/data/sit/_reports/20260513_120000/SRC_A/P327.html",
        blocking=True,
    )
    defaults.update(overrides)
    return FailureRecord(**defaults)


# --------------------------------------------------------------------------- #
# Single-row inserts
# --------------------------------------------------------------------------- #


class TestSingleInsert:
    def test_insert_failure_persists_one_row(self, sqlite_conn, sink):
        sink.insert_failure(_base_record())
        rows = sqlite_conn.execute(
            "SELECT run_id, source, gate_name, layer, error_type, "
            "error_count, blocking FROM VALDO_RUN_FAILURES"
        ).fetchall()
        assert rows == [
            (
                "20260513_120000",
                "SRC_A",
                "L1_structural",
                "L1",
                "structural",
                3,
                1,
            )
        ]

    def test_blocking_bool_persisted_as_int(self, sqlite_conn, sink):
        sink.insert_failure(_base_record(blocking=False))
        (blocking,) = sqlite_conn.execute(
            "SELECT blocking FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert blocking == 0

    def test_failed_at_default_fires_when_omitted(self, sqlite_conn, sink):
        sink.insert_failure(_base_record())
        (failed_at,) = sqlite_conn.execute(
            "SELECT failed_at FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert failed_at is not None and failed_at != ""

    def test_explicit_failed_at_is_honored(self, sqlite_conn, sink):
        ts = datetime(2026, 5, 13, 12, 0, 0)
        sink.insert_failure(_base_record(failed_at=ts))
        (failed_at,) = sqlite_conn.execute(
            "SELECT failed_at FROM VALDO_RUN_FAILURES"
        ).fetchone()
        # SQLite stores datetime as ISO-like string via the qmark binding.
        assert "2026-05-13" in str(failed_at)


# --------------------------------------------------------------------------- #
# failure_detail JSON handling
# --------------------------------------------------------------------------- #


class TestFailureDetail:
    def test_dict_detail_is_json_serialized(self, sqlite_conn, sink):
        sink.insert_failure(
            _base_record(failure_detail={"first_error": "width mismatch", "n": 3})
        )
        (detail,) = sqlite_conn.execute(
            "SELECT failure_detail FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert json.loads(detail) == {"first_error": "width mismatch", "n": 3}

    def test_list_detail_is_json_serialized(self, sqlite_conn, sink):
        sink.insert_failure(
            _base_record(failure_detail=[{"row": 17}, {"row": 42}])
        )
        (detail,) = sqlite_conn.execute(
            "SELECT failure_detail FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert json.loads(detail) == [{"row": 17}, {"row": 42}]

    def test_string_detail_passes_through(self, sqlite_conn, sink):
        sink.insert_failure(_base_record(failure_detail="plain text"))
        (detail,) = sqlite_conn.execute(
            "SELECT failure_detail FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert detail == "plain text"

    def test_none_detail_persisted_as_null(self, sqlite_conn, sink):
        sink.insert_failure(_base_record(failure_detail=None))
        (detail,) = sqlite_conn.execute(
            "SELECT failure_detail FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert detail is None

    def test_unserializable_detail_raises(self, sink):
        class Weird:
            pass

        # default=str salvages most types; explicit non-string, non-JSON
        # primitive that lacks __str__-stable repr still serializes fine
        # in practice. Use a circular reference to provoke a real failure.
        a: list = []
        a.append(a)
        with pytest.raises(FailureSinkError, match="not JSON-serializable"):
            _base_record(failure_detail=a).to_row()


# --------------------------------------------------------------------------- #
# Batch insert & atomicity
# --------------------------------------------------------------------------- #


class TestBatchInsert:
    def test_insert_failures_batch(self, sqlite_conn, sink):
        records = [
            _base_record(gate_name="L1_structural", layer="L1"),
            _base_record(
                gate_name="L2b_sql_truth",
                layer="L2b",
                error_type="compare_diff",
                blocking=False,
            ),
            _base_record(
                gate_name="L3_baseline_diff",
                layer="L3",
                error_type="compare_diff",
                blocking=False,
            ),
        ]
        inserted = sink.insert_failures(records)
        assert inserted == 3
        gates = [
            r[0]
            for r in sqlite_conn.execute(
                "SELECT gate_name FROM VALDO_RUN_FAILURES ORDER BY failure_id"
            )
        ]
        assert gates == ["L1_structural", "L2b_sql_truth", "L3_baseline_diff"]

    def test_insert_failures_empty_is_noop(self, sqlite_conn, sink):
        assert sink.insert_failures([]) == 0
        (count,) = sqlite_conn.execute(
            "SELECT COUNT(*) FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert count == 0

    def test_batch_is_atomic_on_failure(self, sqlite_conn, sink):
        # Force a SQL error by pointing the sink at a non-existent table
        # AFTER inserting one valid record on the real table. The second
        # call must not leave half-written rows.
        sink.insert_failure(_base_record())
        bad_sink = FailureSink.from_connection(
            sqlite_conn, table="NO_SUCH_TABLE", paramstyle="qmark"
        )
        with pytest.raises(FailureSinkError, match="failed to insert"):
            bad_sink.insert_failures(
                [_base_record(gate_name="L2b_sql_truth", layer="L2b")]
            )
        (count,) = sqlite_conn.execute(
            "SELECT COUNT(*) FROM VALDO_RUN_FAILURES"
        ).fetchone()
        assert count == 1  # only the first, valid insert survived


# --------------------------------------------------------------------------- #
# Append-only guarantee
# --------------------------------------------------------------------------- #


class TestAppendOnlyContract:
    def test_public_api_exposes_no_update_or_delete(self):
        public = {name for name in dir(FailureSink) if not name.startswith("_")}
        for forbidden in ("update", "delete", "upsert", "merge", "remove"):
            assert forbidden not in public, (
                f"FailureSink must not expose a {forbidden!r} method "
                "(table is append-only)"
            )

    def test_duplicate_inserts_are_accepted(self, sqlite_conn, sink):
        # Per the module docstring: retries must not poison the trail
        # with ORA-00001 — duplicates are acceptable and traceable.
        record = _base_record()
        sink.insert_failure(record)
        sink.insert_failure(record)
        (count,) = sqlite_conn.execute(
            "SELECT COUNT(*) FROM VALDO_RUN_FAILURES WHERE run_id = ?",
            (record.run_id,),
        ).fetchone()
        assert count == 2


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


class TestRecordValidation:
    @pytest.mark.parametrize(
        "field",
        ["run_id", "env", "source", "pipeline_name", "gate_name", "error_type"],
    )
    def test_required_string_fields_rejected_when_empty(self, field):
        kwargs = dict(
            run_id="r", env="sit", source="s", pipeline_name="p",
            gate_name="g", error_type="structural",
        )
        kwargs[field] = ""
        with pytest.raises(FailureSinkError, match=field):
            FailureRecord(**kwargs)

    def test_invalid_env_rejected(self):
        with pytest.raises(FailureSinkError, match="env must be one of"):
            _base_record(env="prod")

    def test_invalid_layer_rejected(self):
        with pytest.raises(FailureSinkError, match="layer must be one of"):
            _base_record(layer="L4")

    def test_negative_error_count_rejected(self):
        with pytest.raises(FailureSinkError, match="error_count"):
            _base_record(error_count=-1)

    def test_negative_row_count_rejected(self):
        with pytest.raises(FailureSinkError, match="row_count"):
            _base_record(row_count=-5)


# --------------------------------------------------------------------------- #
# Factory & lifecycle
# --------------------------------------------------------------------------- #


class TestFactoryAndLifecycle:
    def test_from_connection_rejects_bad_paramstyle(self, sqlite_conn):
        with pytest.raises(FailureSinkError, match="paramstyle"):
            FailureSink.from_connection(
                sqlite_conn, table="VALDO_RUN_FAILURES", paramstyle="pyformat"
            )

    def test_for_oracle_rejects_unknown_env(self):
        with pytest.raises(FailureSinkError, match="env must be one of"):
            FailureSink.for_oracle(env="prod")

    def test_close_is_idempotent(self, sqlite_conn):
        sink = FailureSink.from_connection(
            sqlite_conn, table="VALDO_RUN_FAILURES", paramstyle="qmark"
        )
        sink.close()
        sink.close()  # second call must not raise

    def test_context_manager_closes(self, sqlite_conn):
        with FailureSink.from_connection(
            sqlite_conn, table="VALDO_RUN_FAILURES", paramstyle="qmark"
        ) as sink:
            sink.insert_failure(_base_record())
        assert sink.connection is None

    def test_default_table_constant(self):
        assert DEFAULT_TABLE == "AUDIT.VALDO_RUN_FAILURES"
