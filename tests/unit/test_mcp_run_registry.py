"""Unit tests for the MCP run registry (S6-1, #386).

Covers both registry implementations:

* :class:`~src.mcp.run_registry.InMemoryRunRegistry` — the legacy
  process-local fallback.
* :class:`~src.mcp.run_registry.DatabaseRunRegistry` — the persistent
  backend wired up against a temporary SQLite engine for speed.

The factory :func:`~src.mcp.run_registry.make_run_registry` is tested
twice:

1. With a working database engine + table (returns DatabaseRunRegistry)
2. With a broken database backend (returns InMemoryRunRegistry,
   logs a WARNING)

We deliberately avoid Oracle here — Oracle is exercised in the
integration suite by ``tests/manual/seed_db.py`` and the manual test
plan. The unit tests should be hermetic.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------------------
# RunRecord roundtrip
# ---------------------------------------------------------------------------


def test_run_record_payload_roundtrip():
    """RunRecord.to_payload + RunRecord.from_payload is lossless."""
    from src.mcp.run_registry import RunRecord

    original = RunRecord(
        run_id="abc123",
        source="SHAW",
        file_path="/tmp/x.dat",
        file_type="TRANERT",
        status="completed",
        started_at="2026-06-15T10:00:00.000000Z",
        finished_at="2026-06-15T10:00:05.000000Z",
        violations=[
            {
                "rule_id": "E001",
                "field": "FOO",
                "severity": "error",
                "message": "bad",
                "record_index": 1,
                "actual_value": "x",
            }
        ],
        error_message=None,
    )

    payload = original.to_payload()
    rebuilt = RunRecord.from_payload(payload)

    assert rebuilt.run_id == original.run_id
    assert rebuilt.source == original.source
    assert rebuilt.file_path == original.file_path
    assert rebuilt.file_type == original.file_type
    assert rebuilt.status == original.status
    assert rebuilt.started_at == original.started_at
    assert rebuilt.finished_at == original.finished_at
    assert rebuilt.violations == original.violations
    assert rebuilt.error_message == original.error_message


def test_run_record_from_payload_rejects_bad_json():
    """Garbage JSON raises ValueError."""
    from src.mcp.run_registry import RunRecord

    with pytest.raises(ValueError):
        RunRecord.from_payload("not json")


def test_run_record_from_payload_rejects_missing_keys():
    """Missing required keys raise KeyError."""
    from src.mcp.run_registry import RunRecord

    with pytest.raises(KeyError):
        RunRecord.from_payload('{"run_id": "x"}')


# ---------------------------------------------------------------------------
# InMemoryRunRegistry
# ---------------------------------------------------------------------------


def _mk_record(run_id: str, source: str = "SHAW", file_path: str = "/tmp/a.dat", status: str = "queued"):
    """Build a minimal RunRecord for tests.

    Args:
        run_id: Identifier to set on the record.
        source: Source name (default ``SHAW``).
        file_path: File path (default ``/tmp/a.dat``).
        status: Lifecycle status (default ``queued``).

    Returns:
        A fully-populated :class:`RunRecord`.
    """
    from src.mcp.run_registry import RunRecord

    return RunRecord(
        run_id=run_id,
        source=source,
        file_path=file_path,
        file_type=None,
        status=status,
        started_at="2026-06-15T10:00:00.000000Z",
    )


def test_inmemory_put_get_roundtrip():
    """put -> get returns the same record."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    rec = _mk_record("run-1")
    reg.put(rec)

    fetched = reg.get("run-1")
    assert fetched is not None
    assert fetched.run_id == "run-1"
    assert fetched.source == "SHAW"


def test_inmemory_get_missing_returns_none():
    """get on an unknown run_id returns None."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    assert reg.get("does-not-exist") is None


def test_inmemory_find_inflight_skips_terminal():
    """find_inflight ignores records in terminal states."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    reg.put(_mk_record("done", status="completed"))
    reg.put(_mk_record("live", status="running"))

    found = reg.find_inflight("SHAW", "/tmp/a.dat")
    assert found is not None
    assert found.run_id == "live"


def test_inmemory_find_inflight_match_requires_both_keys():
    """find_inflight matches only when source AND file_path are both equal."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    reg.put(_mk_record("r1", source="SHAW", file_path="/tmp/a.dat", status="running"))
    reg.put(_mk_record("r2", source="ENCORE", file_path="/tmp/a.dat", status="running"))
    reg.put(_mk_record("r3", source="SHAW", file_path="/tmp/b.dat", status="running"))

    assert reg.find_inflight("SHAW", "/tmp/a.dat").run_id == "r1"
    assert reg.find_inflight("ENCORE", "/tmp/a.dat").run_id == "r2"
    assert reg.find_inflight("SHAW", "/tmp/b.dat").run_id == "r3"
    assert reg.find_inflight("DOES_NOT_EXIST", "/tmp/a.dat") is None


def test_inmemory_clear_drops_all():
    """clear empties the registry — used by test fixtures."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    reg.put(_mk_record("r1"))
    reg.put(_mk_record("r2"))
    reg.clear()

    assert reg.get("r1") is None
    assert reg.get("r2") is None


# ---------------------------------------------------------------------------
# DatabaseRunRegistry — SQLite for hermetic tests
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_engine() -> Iterator:
    """Return a fresh SQLAlchemy engine + ensure the table exists.

    Builds an on-disk SQLite database in a temp dir so the
    ``DatabaseRunRegistry`` can issue its cross-dialect upsert SQL
    against a real connection. The directory is torn down after the test.

    Yields:
        Tuple of (engine, schema_prefix). schema_prefix is empty for
        SQLite — see :func:`~src.database.db_url.get_valdo_schema`.
    """
    tmpdir = tempfile.mkdtemp(prefix="valdo_mcp_reg_")
    db_path = Path(tmpdir) / "test.db"
    engine = create_engine(f"sqlite:///{db_path}")

    # Create the table directly — we don't want to depend on running
    # Alembic in a unit test (Alembic upgrades are exercised in the
    # integration suite).
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE APP_MCP_RUN_REGISTRY ("
            "run_id TEXT PRIMARY KEY, "
            "source TEXT NOT NULL, "
            "file_path TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "started_at TIMESTAMP NOT NULL, "
            "finished_at TIMESTAMP, "
            "violation_count INTEGER, "
            "payload TEXT NOT NULL, "
            "last_heartbeat_at TIMESTAMP, "
            "attempt_count INTEGER DEFAULT 0, "
            "created_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        # S10-2 (#397): dedicated worker-liveness table (Alembic 0007).
        conn.execute(text(
            "CREATE TABLE MCP_WORKERS ("
            "worker_id TEXT PRIMARY KEY, "
            "host TEXT, "
            "started_at TIMESTAMP NOT NULL, "
            "last_heartbeat_at TIMESTAMP NOT NULL)"
        ))

    # Force DB_ADAPTER=sqlite so the registry uses the INSERT OR REPLACE
    # path. We restore the original on teardown.
    old_adapter = os.environ.get("DB_ADAPTER")
    os.environ["DB_ADAPTER"] = "sqlite"

    try:
        yield engine, ""
    finally:
        engine.dispose()
        if old_adapter is None:
            os.environ.pop("DB_ADAPTER", None)
        else:
            os.environ["DB_ADAPTER"] = old_adapter
        # Cleanup
        try:
            db_path.unlink()
        except OSError:
            pass


def test_db_registry_put_get_roundtrip(sqlite_engine):
    """put -> get returns the same record from the SQLite backend."""
    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    rec = _mk_record("db-run-1")
    reg.put(rec)

    fetched = reg.get("db-run-1")
    assert fetched is not None
    assert fetched.run_id == "db-run-1"
    assert fetched.source == "SHAW"
    assert fetched.status == "queued"


def test_db_registry_put_upsert_overwrites(sqlite_engine):
    """A second put with the same run_id replaces the previous payload."""
    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(_mk_record("dup-run", status="queued"))
    reg.put(_mk_record("dup-run", status="completed"))

    fetched = reg.get("dup-run")
    assert fetched.status == "completed"


def test_db_registry_get_missing_returns_none(sqlite_engine):
    """get on an absent run_id returns None (no exception)."""
    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    assert reg.get("does-not-exist") is None


def test_db_registry_find_inflight_skips_terminal(sqlite_engine):
    """find_inflight ignores terminal rows."""
    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(_mk_record("done", status="completed"))
    reg.put(_mk_record("live", status="running"))

    found = reg.find_inflight("SHAW", "/tmp/a.dat")
    assert found is not None
    assert found.run_id == "live"


def test_db_registry_find_inflight_no_match(sqlite_engine):
    """find_inflight returns None when nothing matches."""
    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    assert reg.find_inflight("SHAW", "/tmp/x.dat") is None


def test_db_registry_payload_carries_violations(sqlite_engine):
    """Round-tripping a terminal record preserves the violations list."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    rec = RunRecord(
        run_id="r-with-viols",
        source="SHAW",
        file_path="/tmp/a.dat",
        file_type=None,
        status="completed",
        started_at="2026-06-15T10:00:00.000000Z",
        finished_at="2026-06-15T10:00:01.000000Z",
        violations=[
            {"rule_id": "E1", "severity": "error", "message": "x"},
            {"rule_id": "W1", "severity": "warning", "message": "y"},
        ],
    )
    reg.put(rec)

    fetched = reg.get("r-with-viols")
    assert fetched.status == "completed"
    assert len(fetched.violations) == 2
    assert fetched.violations[0]["rule_id"] == "E1"


def test_db_registry_clear_empties_table(sqlite_engine):
    """clear() drops every row."""
    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(_mk_record("r1"))
    reg.put(_mk_record("r2"))
    reg.clear()

    assert reg.get("r1") is None
    assert reg.get("r2") is None


# ---------------------------------------------------------------------------
# Factory: make_run_registry
# ---------------------------------------------------------------------------


def test_factory_returns_inmemory_when_engine_construction_fails(monkeypatch, caplog):
    """make_run_registry falls back to in-memory when the engine raises.

    Simulated by monkeypatching ``get_engine`` to raise. The factory
    must log a WARNING and return an :class:`InMemoryRunRegistry` rather
    than propagating the error — refusing to boot would block the whole
    MCP surface.
    """
    import src.database.engine as engine_mod
    from src.mcp.run_registry import InMemoryRunRegistry, make_run_registry

    def _raise():
        raise RuntimeError("simulated DB outage")

    monkeypatch.setattr(engine_mod, "get_engine", _raise)

    with caplog.at_level(logging.WARNING, logger="src.mcp.run_registry"):
        reg = make_run_registry()

    assert isinstance(reg, InMemoryRunRegistry)
    assert any("falling back to in-memory" in r.message for r in caplog.records), (
        f"Expected fallback WARNING; got records: {[r.message for r in caplog.records]!r}"
    )


def test_factory_returns_inmemory_when_table_missing(monkeypatch, caplog):
    """make_run_registry falls back when the engine works but the table is missing.

    Construct an in-memory SQLite engine with no APP_MCP_RUN_REGISTRY
    table, point ``get_engine`` at it, and verify the factory probes the
    table, catches the missing-table error, and falls back to memory.
    """
    import src.database.engine as engine_mod
    import src.database.db_url as db_url_mod
    from src.mcp.run_registry import InMemoryRunRegistry, make_run_registry

    # Build a SQLite engine with NO table.
    bare_engine = create_engine("sqlite:///:memory:")
    monkeypatch.setattr(engine_mod, "get_engine", lambda: bare_engine)
    monkeypatch.setattr(db_url_mod, "get_valdo_schema", lambda: "")

    with caplog.at_level(logging.WARNING, logger="src.mcp.run_registry"):
        reg = make_run_registry()

    assert isinstance(reg, InMemoryRunRegistry)
    assert any("falling back to in-memory" in r.message for r in caplog.records)


def test_factory_returns_database_when_table_present(monkeypatch, caplog):
    """make_run_registry returns DatabaseRunRegistry when the probe succeeds."""
    import src.database.engine as engine_mod
    import src.database.db_url as db_url_mod
    from src.mcp.run_registry import DatabaseRunRegistry, make_run_registry

    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE APP_MCP_RUN_REGISTRY ("
            "run_id TEXT PRIMARY KEY, "
            "source TEXT, "
            "file_path TEXT, "
            "status TEXT, "
            "started_at TIMESTAMP, "
            "finished_at TIMESTAMP, "
            "violation_count INTEGER, "
            "payload TEXT, "
            "created_ts TIMESTAMP)"
        ))

    monkeypatch.setattr(engine_mod, "get_engine", lambda: engine)
    monkeypatch.setattr(db_url_mod, "get_valdo_schema", lambda: "")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")

    with caplog.at_level(logging.INFO, logger="src.mcp.run_registry"):
        reg = make_run_registry()

    assert isinstance(reg, DatabaseRunRegistry)
    assert any("using database backend" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# claim_next — atomic queued -> running transition (S9-5, #391, ADR 0021)
# ---------------------------------------------------------------------------


def test_inmemory_claim_next_returns_oldest_queued():
    """claim_next claims the oldest queued row and flips it to running."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="old", source="SHAW", file_path="/tmp/a.dat", file_type=None,
        status="queued", started_at="2026-06-15T10:00:00.000000Z",
    ))
    reg.put(RunRecord(
        run_id="new", source="SHAW", file_path="/tmp/b.dat", file_type=None,
        status="queued", started_at="2026-06-15T10:05:00.000000Z",
    ))

    claimed = reg.claim_next()
    assert claimed is not None
    assert claimed.run_id == "old"
    assert claimed.status == "running"
    # The persisted row must reflect the running transition.
    assert reg.get("old").status == "running"


def test_inmemory_claim_next_empty_returns_none():
    """claim_next on an empty (or no-queued) registry returns None."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    assert reg.claim_next() is None


def test_inmemory_claim_next_skips_non_queued():
    """claim_next ignores running/completed/failed rows."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="r1", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at="2026-06-15T10:00:00.000000Z",
    ))
    reg.put(RunRecord(
        run_id="r2", source="S", file_path="/tmp/b", file_type=None,
        status="completed", started_at="2026-06-15T10:01:00.000000Z",
    ))
    assert reg.claim_next() is None


def test_inmemory_claim_next_atomicity_two_claimers_one_winner():
    """Two claims against ONE queued row never return the same job twice.

    Either two distinct jobs are claimed (when >1 queued exist) or one
    claimer wins and the other gets ``None`` (when exactly one queued
    exists). This is the in-memory analogue of the DB row-claim race.
    """
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="only", source="S", file_path="/tmp/a", file_type=None,
        status="queued", started_at="2026-06-15T10:00:00.000000Z",
    ))

    first = reg.claim_next()
    second = reg.claim_next()

    assert first is not None and first.run_id == "only"
    assert second is None  # already claimed -> no longer queued


def test_db_claim_next_returns_oldest_queued(sqlite_engine):
    """claim_next on the SQLite backend claims + flips the oldest queued row."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(RunRecord(
        run_id="old", source="SHAW", file_path="/tmp/a.dat", file_type=None,
        status="queued", started_at="2026-06-15T10:00:00.000000Z",
    ))
    reg.put(RunRecord(
        run_id="new", source="SHAW", file_path="/tmp/b.dat", file_type=None,
        status="queued", started_at="2026-06-15T10:05:00.000000Z",
    ))

    claimed = reg.claim_next()
    assert claimed is not None
    assert claimed.run_id == "old"
    assert claimed.status == "running"
    assert reg.get("old").status == "running"
    # The younger row is untouched.
    assert reg.get("new").status == "queued"


def test_db_claim_next_no_queued_returns_none(sqlite_engine):
    """claim_next returns None when no queued rows exist."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(RunRecord(
        run_id="done", source="S", file_path="/tmp/a", file_type=None,
        status="completed", started_at="2026-06-15T10:00:00.000000Z",
    ))
    assert reg.claim_next() is None


def test_db_claim_next_atomicity_two_claimers_one_winner(sqlite_engine):
    """Two sequential claims of a single queued row: one wins, one gets None.

    The guarded ``UPDATE ... WHERE status='queued'`` means the second
    claim sees the row already ``running`` and must not re-claim it — the
    same job is never handed to two workers.
    """
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(RunRecord(
        run_id="only", source="S", file_path="/tmp/a", file_type=None,
        status="queued", started_at="2026-06-15T10:00:00.000000Z",
    ))

    first = reg.claim_next()
    second = reg.claim_next()

    assert first is not None and first.run_id == "only"
    assert second is None


def test_db_claim_next_two_queued_yields_two_distinct(sqlite_engine):
    """Two queued rows -> two claims return two DIFFERENT jobs."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(RunRecord(
        run_id="j1", source="S", file_path="/tmp/a", file_type=None,
        status="queued", started_at="2026-06-15T10:00:00.000000Z",
    ))
    reg.put(RunRecord(
        run_id="j2", source="S", file_path="/tmp/b", file_type=None,
        status="queued", started_at="2026-06-15T10:01:00.000000Z",
    ))

    first = reg.claim_next()
    second = reg.claim_next()
    third = reg.claim_next()

    claimed_ids = {first.run_id, second.run_id}
    assert claimed_ids == {"j1", "j2"}, f"expected both jobs, got {claimed_ids!r}"
    assert third is None


# ---------------------------------------------------------------------------
# RunRecord round-trip with the S10-1 heartbeat/attempt fields
# ---------------------------------------------------------------------------


def test_run_record_roundtrip_with_heartbeat_and_attempt():
    """to_payload/from_payload preserve last_heartbeat_at + attempt_count."""
    from src.mcp.run_registry import RunRecord

    original = RunRecord(
        run_id="hb-1",
        source="SHAW",
        file_path="/tmp/x.dat",
        file_type=None,
        status="running",
        started_at="2026-06-15T10:00:00.000000Z",
        last_heartbeat_at="2026-06-15T10:00:30.000000Z",
        attempt_count=2,
    )

    rebuilt = RunRecord.from_payload(original.to_payload())

    assert rebuilt.last_heartbeat_at == "2026-06-15T10:00:30.000000Z"
    assert rebuilt.attempt_count == 2


def test_run_record_roundtrip_defaults_for_legacy_payload():
    """A payload from before S10-1 (no new keys) round-trips with defaults."""
    from src.mcp.run_registry import RunRecord

    legacy = (
        '{"run_id": "old", "source": "S", "file_path": "/tmp/a", '
        '"file_type": null, "status": "queued", '
        '"started_at": "2026-06-15T10:00:00.000000Z"}'
    )
    rebuilt = RunRecord.from_payload(legacy)

    assert rebuilt.last_heartbeat_at is None
    assert rebuilt.attempt_count == 0


# ---------------------------------------------------------------------------
# heartbeat — refresh last_heartbeat_at (S10-1, #397, ADR 0021)
# ---------------------------------------------------------------------------


def test_inmemory_heartbeat_updates_last_heartbeat_at():
    """heartbeat sets last_heartbeat_at on the in-memory record."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="hb", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at="2026-06-15T10:00:00.000000Z",
    ))
    assert reg.get("hb").last_heartbeat_at is None

    reg.heartbeat("hb")

    assert reg.get("hb").last_heartbeat_at is not None


def test_inmemory_heartbeat_unknown_run_is_noop():
    """heartbeat on an unknown run_id does not raise."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    reg.heartbeat("nope")  # must not raise


def test_db_heartbeat_updates_last_heartbeat_at(sqlite_engine):
    """heartbeat refreshes last_heartbeat_at in the SQLite backend."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)
    reg.put(RunRecord(
        run_id="hb", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at="2026-06-15T10:00:00.000000Z",
    ))

    reg.heartbeat("hb")

    fetched = reg.get("hb")
    assert fetched.last_heartbeat_at is not None


def test_db_claim_next_sets_initial_heartbeat(sqlite_engine):
    """claim_next stamps an initial heartbeat at claim time."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)
    reg.put(RunRecord(
        run_id="c1", source="S", file_path="/tmp/a", file_type=None,
        status="queued", started_at="2026-06-15T10:00:00.000000Z",
    ))

    claimed = reg.claim_next()
    assert claimed is not None
    assert claimed.last_heartbeat_at is not None
    assert reg.get("c1").last_heartbeat_at is not None


# ---------------------------------------------------------------------------
# reap_stuck — reclaim stale running rows back to queued (S10-1, #397)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402


def _iso(dt: datetime) -> str:
    """Render a datetime as the registry's ISO-8601 UTC string."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def test_inmemory_reap_stale_heartbeat_requeues_and_increments():
    """A running row with a stale heartbeat is reclaimed to queued, attempt++."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="stale", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(hours=1)),
        last_heartbeat_at=_iso(now - timedelta(seconds=600)),
        attempt_count=0,
    ))

    reclaimed = reg.reap_stuck(stale_before=stale_before, now=now)

    assert reclaimed == 1
    rec = reg.get("stale")
    assert rec.status == "queued"
    assert rec.attempt_count == 1
    assert rec.last_heartbeat_at is None


def test_inmemory_reap_fresh_heartbeat_left_running():
    """A running row with a fresh heartbeat is NOT reaped (long job survives)."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="fresh", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(hours=1)),
        last_heartbeat_at=_iso(now - timedelta(seconds=5)),
    ))

    reclaimed = reg.reap_stuck(stale_before=stale_before, now=now)

    assert reclaimed == 0
    assert reg.get("fresh").status == "running"


def test_inmemory_reap_null_heartbeat_old_start_reclaimed():
    """A running row with NULL heartbeat and an old started_at is reclaimed."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="nullhb", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(seconds=600)),
        last_heartbeat_at=None,
    ))

    reclaimed = reg.reap_stuck(stale_before=stale_before, now=now)

    assert reclaimed == 1
    assert reg.get("nullhb").status == "queued"


def test_inmemory_reap_null_heartbeat_fresh_start_left_running():
    """NULL heartbeat but a recent started_at is NOT reaped."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="nullfresh", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(seconds=10)),
        last_heartbeat_at=None,
    ))

    reclaimed = reg.reap_stuck(stale_before=stale_before, now=now)

    assert reclaimed == 0
    assert reg.get("nullfresh").status == "running"


def test_inmemory_reap_ignores_terminal_and_queued():
    """reap_stuck only touches running rows."""
    from src.mcp.run_registry import InMemoryRunRegistry, RunRecord

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)
    old = _iso(now - timedelta(hours=1))

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(run_id="q", source="S", file_path="/a", file_type=None,
                      status="queued", started_at=old))
    reg.put(RunRecord(run_id="c", source="S", file_path="/b", file_type=None,
                      status="completed", started_at=old))

    assert reg.reap_stuck(stale_before=stale_before, now=now) == 0
    assert reg.get("q").status == "queued"
    assert reg.get("c").status == "completed"


def test_db_reap_stale_heartbeat_requeues_and_increments(sqlite_engine):
    """SQLite backend: stale-heartbeat running row reclaimed, attempt++."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    reg.put(RunRecord(
        run_id="stale", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(hours=1)),
        last_heartbeat_at=_iso(now - timedelta(seconds=600)),
        attempt_count=1,
    ))

    reclaimed = reg.reap_stuck(stale_before=stale_before, now=now)

    assert reclaimed == 1
    rec = reg.get("stale")
    assert rec.status == "queued"
    assert rec.attempt_count == 2
    assert rec.last_heartbeat_at is None


def test_db_reap_fresh_heartbeat_left_running(sqlite_engine):
    """SQLite backend: fresh-heartbeat row survives (long job not reaped)."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    reg.put(RunRecord(
        run_id="fresh", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(hours=1)),
        last_heartbeat_at=_iso(now - timedelta(seconds=5)),
    ))

    assert reg.reap_stuck(stale_before=stale_before, now=now) == 0
    assert reg.get("fresh").status == "running"


def test_db_reap_null_heartbeat_old_start_reclaimed(sqlite_engine):
    """SQLite backend: NULL heartbeat + old start is reclaimed."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    reg.put(RunRecord(
        run_id="nullhb", source="S", file_path="/tmp/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(seconds=600)),
        last_heartbeat_at=None,
    ))

    assert reg.reap_stuck(stale_before=stale_before, now=now) == 1
    assert reg.get("nullhb").status == "queued"


# ---------------------------------------------------------------------------
# Worker liveness: register_worker / has_live_worker (S10-2, #397)
# ---------------------------------------------------------------------------
#
# A dedicated MCP_WORKERS table (Alembic 0007) tracks each worker process's
# last heartbeat. ``validate_file`` consults ``has_live_worker(window)`` to
# decide whether ANY worker is draining the queue — if so it enqueues fast;
# if not it falls back to a synchronous inline run so nothing is stranded.


def test_inmemory_register_worker_then_live():
    """A freshly-registered worker is reported live within the window."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    assert reg.has_live_worker(within_seconds=60) is False

    reg.register_worker("worker-1", host="box-a")

    assert reg.has_live_worker(within_seconds=60) is True


def test_inmemory_register_worker_refreshes_heartbeat():
    """A second register_worker call refreshes the heartbeat (upsert, no dup)."""
    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    reg.register_worker("worker-1")
    reg.register_worker("worker-1")  # idempotent upsert

    assert reg.has_live_worker(within_seconds=60) is True


def test_inmemory_has_live_worker_stale_is_dead():
    """A worker whose heartbeat is older than the window is NOT live."""
    from datetime import datetime, timedelta, timezone

    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    reg.register_worker("worker-1")

    # Backdate the worker's heartbeat well outside the window.
    stale = datetime.now(timezone.utc) - timedelta(seconds=600)
    reg._workers["worker-1"].last_heartbeat_at = _iso(stale)

    assert reg.has_live_worker(within_seconds=60) is False


def test_inmemory_has_live_worker_any_live_returns_true():
    """has_live_worker is True if ANY worker is live, even when others are stale."""
    from datetime import datetime, timedelta, timezone

    from src.mcp.run_registry import InMemoryRunRegistry

    reg = InMemoryRunRegistry()
    reg.register_worker("dead")
    reg.register_worker("alive")

    stale = datetime.now(timezone.utc) - timedelta(seconds=600)
    reg._workers["dead"].last_heartbeat_at = _iso(stale)

    assert reg.has_live_worker(within_seconds=60) is True


def test_db_register_worker_then_live(sqlite_engine):
    """SQLite backend: a registered worker is reported live within the window."""
    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    assert reg.has_live_worker(within_seconds=60) is False

    reg.register_worker("worker-1", host="box-a")

    assert reg.has_live_worker(within_seconds=60) is True


def test_db_register_worker_upsert_no_duplicate(sqlite_engine):
    """SQLite backend: re-registering the same worker_id upserts (one row)."""
    from sqlalchemy import text as _text

    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.register_worker("worker-1", host="box-a")
    reg.register_worker("worker-1", host="box-a")

    with engine.connect() as conn:
        count = conn.execute(
            _text("SELECT COUNT(*) FROM MCP_WORKERS WHERE worker_id = 'worker-1'")
        ).scalar()
    assert count == 1
    assert reg.has_live_worker(within_seconds=60) is True


def test_db_has_live_worker_stale_is_dead(sqlite_engine):
    """SQLite backend: a worker with a stale heartbeat is not live."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import text as _text

    from src.mcp.run_registry import DatabaseRunRegistry

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)
    reg.register_worker("worker-1")

    # Backdate the heartbeat directly so it falls outside any reasonable window.
    stale = datetime.now(timezone.utc) - timedelta(seconds=600)
    with engine.begin() as conn:
        conn.execute(
            _text("UPDATE MCP_WORKERS SET last_heartbeat_at = :hb WHERE worker_id = 'worker-1'"),
            {"hb": stale},
        )

    assert reg.has_live_worker(within_seconds=60) is False


# ---------------------------------------------------------------------------
# S17-3 (#430): bounded claim_next / reap_stuck scans
# ---------------------------------------------------------------------------


def test_db_claim_next_fetches_one_candidate_not_whole_queue(sqlite_engine):
    """claim_next issues a bounded single-candidate SELECT, not a full scan.

    With a deep queue, the candidate-selection SELECT must carry a single-row
    limit clause (``FETCH FIRST 1 ROW ONLY`` / ``LIMIT 1``) so the registry
    never pulls the entire queued set into memory per claim. We assert on the
    emitted SQL via a Connection.execute spy.
    """
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    for i in range(25):
        reg.put(RunRecord(
            run_id=f"q-{i:03d}", source="S", file_path=f"/tmp/{i}", file_type=None,
            status="queued",
            started_at=f"2026-06-15T10:{i // 60:02d}:{i % 60:02d}.000000Z",
        ))

    captured: list[str] = []
    from sqlalchemy.engine import Connection

    orig = Connection.execute

    def _spy(self, clause, *args, **kwargs):
        captured.append(str(clause))
        return orig(self, clause, *args, **kwargs)

    Connection.execute = _spy
    try:
        claimed = reg.claim_next()
    finally:
        Connection.execute = orig

    assert claimed is not None
    select_sql = [
        s for s in captured if "SELECT run_id" in s and "status = 'queued'" in s
    ]
    assert select_sql, "expected a queued-candidate SELECT to be issued"
    assert any(
        ("FETCH FIRST" in s) or ("LIMIT" in s) for s in select_sql
    ), f"candidate SELECT was not bounded: {select_sql!r}"


def test_db_claim_next_drains_queue_one_at_a_time(sqlite_engine):
    """N queued rows drain via N successive claim_next calls; N+1 returns None."""
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    n = 10
    for i in range(n):
        reg.put(RunRecord(
            run_id=f"d-{i:03d}", source="S", file_path=f"/tmp/{i}", file_type=None,
            status="queued",
            started_at=f"2026-06-15T10:00:{i:02d}.000000Z",
        ))

    claimed = []
    for _ in range(n):
        rec = reg.claim_next()
        assert rec is not None
        claimed.append(rec.run_id)

    assert reg.claim_next() is None
    assert set(claimed) == {f"d-{i:03d}" for i in range(n)}


def test_db_claim_next_lose_the_race_retries_next_candidate(sqlite_engine):
    """If the single candidate is stolen before its UPDATE, retry the next one.

    Pre-steals the oldest candidate (flips it to running) so its guarded
    UPDATE matches zero rows. The claim must NOT return None while the queue
    still holds a claimable row — it must fall through to the next candidate.
    """
    from src.mcp.run_registry import DatabaseRunRegistry, RunRecord

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    reg.put(RunRecord(
        run_id="first", source="S", file_path="/tmp/a", file_type=None,
        status="queued", started_at="2026-06-15T10:00:00.000000Z",
    ))
    reg.put(RunRecord(
        run_id="second", source="S", file_path="/tmp/b", file_type=None,
        status="queued", started_at="2026-06-15T10:00:01.000000Z",
    ))

    with engine.begin() as conn:
        conn.execute(
            text(
                f"UPDATE {reg._table} SET status = 'running' WHERE run_id = 'first'"
            )
        )

    claimed = reg.claim_next()
    assert claimed is not None, "claim returned None while 'second' was claimable"
    assert claimed.run_id == "second"
    assert claimed.status == "running"


def test_db_reap_stuck_is_bounded(sqlite_engine):
    """reap_stuck processes a bounded batch and leaves fresh rows untouched."""
    from src.mcp.run_registry import (
        DatabaseRunRegistry,
        RunRecord,
        _REAP_BATCH_SIZE,
    )

    engine, schema = sqlite_engine
    reg = DatabaseRunRegistry(engine=engine, schema_prefix=schema)

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    stale_before = now - timedelta(seconds=300)

    stale_total = _REAP_BATCH_SIZE + 5
    for i in range(stale_total):
        reg.put(RunRecord(
            run_id=f"stale-{i:03d}", source="S", file_path=f"/tmp/{i}",
            file_type=None, status="running",
            started_at=_iso(now - timedelta(hours=1)),
            last_heartbeat_at=_iso(now - timedelta(seconds=600)),
        ))
    reg.put(RunRecord(
        run_id="fresh", source="S", file_path="/tmp/fresh", file_type=None,
        status="running", started_at=_iso(now - timedelta(hours=1)),
        last_heartbeat_at=_iso(now - timedelta(seconds=5)),
    ))

    reclaimed = reg.reap_stuck(stale_before=stale_before, now=now)

    assert reclaimed <= _REAP_BATCH_SIZE
    assert reclaimed == _REAP_BATCH_SIZE
    assert reg.get("fresh").status == "running"

    total = reclaimed
    for _ in range(5):
        more = reg.reap_stuck(stale_before=stale_before, now=now)
        total += more
        if more == 0:
            break
    assert total == stale_total
    assert reg.get("fresh").status == "running"
