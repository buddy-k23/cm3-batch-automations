"""Multi-worker concurrency proof for ``claim_next`` (S10-2, #397, ADR 0021).

Sprint 9's ``DatabaseRunRegistry.claim_next`` performs the guarded
``UPDATE ... SET status='running' WHERE status='queued'`` and treats a
``rowcount == 1`` as the win. This test spins up N worker threads, each
looping ``claim_next`` against ONE shared on-disk SQLite registry that holds
M queued jobs, and asserts:

* every queued job is claimed exactly once (no double-claim),
* no job is dropped (the union of claimed ids == all M),
* the per-thread claim sets are disjoint.

On-disk SQLite (not ``:memory:``) is used deliberately so the connections
opened by the shared engine across threads see the *same* database file and
exercise real row-level locking / busy-handling — an in-memory database is
per-connection and would not prove cross-connection safety.

This is the production backstop for the "multi-worker double-claim under real
concurrency" risk flagged in the Sprint 10 kickoff.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Dict, List

import pytest
from sqlalchemy import create_engine, text


@pytest.fixture
def shared_sqlite_registry():
    """Yield a DatabaseRunRegistry over an on-disk SQLite db (real locking).

    A higher pool size + a generous busy timeout lets concurrent worker
    threads contend on the same file without spurious "database is locked"
    errors, which is the realistic INT/prod posture (Oracle/Postgres handle
    this natively; SQLite needs the busy timeout).

    Yields:
        The constructed :class:`DatabaseRunRegistry`.
    """
    from src.mcp.run_registry import DatabaseRunRegistry

    tmpdir = tempfile.mkdtemp(prefix="valdo_multi_worker_")
    db_path = Path(tmpdir) / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 30, "check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE APP_MCP_RUN_REGISTRY ("
            "run_id TEXT PRIMARY KEY, source TEXT NOT NULL, "
            "file_path TEXT NOT NULL, status TEXT NOT NULL, "
            "started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP, "
            "violation_count INTEGER, payload TEXT NOT NULL, "
            "last_heartbeat_at TIMESTAMP, attempt_count INTEGER DEFAULT 0, "
            "created_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))

    import os
    old_adapter = os.environ.get("DB_ADAPTER")
    os.environ["DB_ADAPTER"] = "sqlite"
    try:
        yield DatabaseRunRegistry(engine=engine, schema_prefix="")
    finally:
        engine.dispose()
        if old_adapter is None:
            os.environ.pop("DB_ADAPTER", None)
        else:
            os.environ["DB_ADAPTER"] = old_adapter
        try:
            db_path.unlink()
        except OSError:
            pass


def test_multi_worker_no_double_claim(shared_sqlite_registry):
    """N threads draining M queued jobs claim each job exactly once.

    Asserts no double-claim (a run_id appearing in two threads' results),
    no dropped job (union of claims == all enqueued ids), and that each
    thread's claimed set is disjoint from the others.
    """
    from src.mcp.run_registry import RunRecord

    reg = shared_sqlite_registry

    num_jobs = 60
    num_workers = 8

    # Enqueue M queued jobs with monotonically increasing started_at so the
    # ORDER BY started_at in claim_next has a deterministic order to drain.
    all_ids = set()
    for i in range(num_jobs):
        run_id = f"job-{i:04d}"
        all_ids.add(run_id)
        reg.put(RunRecord(
            run_id=run_id,
            source="S",
            file_path=f"/tmp/{i}.dat",
            file_type=None,
            status="queued",
            started_at=f"2026-06-15T10:{i // 60:02d}:{i % 60:02d}.000000Z",
        ))

    claimed_by_thread: Dict[int, List[str]] = {w: [] for w in range(num_workers)}
    start_gate = threading.Event()

    def _worker(worker_idx: int) -> None:
        """Loop claim_next until the queue is drained, recording wins."""
        start_gate.wait()
        while True:
            record = reg.claim_next()
            if record is None:
                # Empty *right now* — but another thread may be between its
                # SELECT and UPDATE. Re-poll a bounded number of times to be
                # sure the queue is genuinely drained, not transiently empty.
                drained = True
                for _ in range(3):
                    retry = reg.claim_next()
                    if retry is not None:
                        claimed_by_thread[worker_idx].append(retry.run_id)
                        drained = False
                        break
                if drained:
                    return
                continue
            claimed_by_thread[worker_idx].append(record.run_id)

    threads = [
        threading.Thread(target=_worker, args=(w,), name=f"worker-{w}")
        for w in range(num_workers)
    ]
    for t in threads:
        t.start()
    start_gate.set()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive(), "worker thread did not finish draining in time"

    # Flatten all claims.
    flat = [rid for claims in claimed_by_thread.values() for rid in claims]

    # No double-claim: every run_id appears at most once across all threads.
    assert len(flat) == len(set(flat)), (
        f"double-claim detected: {len(flat)} claims but {len(set(flat))} unique"
    )

    # No dropped job: the union of claimed ids equals the full enqueued set.
    assert set(flat) == all_ids, (
        f"missing={all_ids - set(flat)!r} extra={set(flat) - all_ids!r}"
    )

    # Disjoint per-thread sets (a stronger restatement of "no double-claim").
    seen = set()
    for w, claims in claimed_by_thread.items():
        s = set(claims)
        assert s.isdisjoint(seen), f"worker {w} re-claimed {s & seen!r}"
        seen |= s

    # Every job ended up 'running' (claimed) — none left 'queued'.
    for run_id in all_ids:
        assert reg.get(run_id).status == "running", f"{run_id} not claimed"
