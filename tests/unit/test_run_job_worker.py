"""Unit tests for the ``valdo run-job-worker`` skeleton (S9-5, #391).

Covers the thin background-job skeleton defined by ADR 0021:

1. ``drain_once`` claims a single queued job, runs the (stubbed) validate
   body, and drives the record to a terminal ``completed`` state.
2. ``drain_once`` on an empty queue returns 0 (nothing claimed) and does
   not call the engine.
3. A failing engine drives the claimed record to ``failed`` (the worker
   never lets an engine error escape).
4. ``validate_file_payload`` with ``VALDO_MCP_ASYNC_VALIDATE`` ON ENQUEUES
   only — the record is left ``queued`` and the engine is NOT called
   inline — then the worker drains it to ``completed``.
5. The S10-1 runtime: the continuous poll loop (fake clock + fake sleep),
   graceful shutdown via an injected flag, the stuck-``running`` reaper
   (delegating to ``registry.reap_stuck``), and — crucially — the during-run
   heartbeat thread that keeps a long job from being falsely reaped.

These run entirely against the in-memory registry with a stubbed
``run_validate_service`` so they are hermetic and require no database.
"""

from __future__ import annotations

import time
from typing import Any, Dict

import pytest

from src.mcp.run_registry import InMemoryRunRegistry, RunRecord


def _queued_record(run_id: str, source: str = "SHAW", file_path: str = "/tmp/a.dat") -> RunRecord:
    """Build a queued RunRecord for worker tests."""
    return RunRecord(
        run_id=run_id,
        source=source,
        file_path=file_path,
        file_type=None,
        status="queued",
        started_at="2026-06-15T10:00:00.000000Z",
    )


@pytest.fixture
def stub_engine(monkeypatch):
    """Stub ``run_validate_service`` to a deterministic clean result.

    The worker imports the symbol lazily on the source module (mirroring
    action_tools), so we patch it there.
    """
    import src.services.validate_service as svc

    calls: Dict[str, Any] = {"count": 0, "last_file": None}

    def _stub(**kwargs):
        calls["count"] += 1
        calls["last_file"] = kwargs.get("file")
        return {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": [],
            "total_rows": 1,
        }

    monkeypatch.setattr(svc, "run_validate_service", _stub)
    return calls


@pytest.fixture
def stub_artefacts(monkeypatch):
    """Stub artefact resolution so the worker need not read a source overlay."""
    import src.commands.run_job_worker as worker

    monkeypatch.setattr(
        worker,
        "_resolve_artefacts",
        lambda source, file_path, file_type: {"mapping": None, "rules": None, "file_type": file_type},
    )


def test_drain_once_claims_and_completes(stub_engine, stub_artefacts):
    """drain_once claims a queued job and drives it to completed."""
    from src.commands.run_job_worker import drain_once

    reg = InMemoryRunRegistry()
    reg.put(_queued_record("job-1"))

    claimed = drain_once(reg)

    assert claimed == 1
    assert stub_engine["count"] == 1
    assert stub_engine["last_file"] == "/tmp/a.dat"
    assert reg.get("job-1").status == "completed"


def test_drain_once_empty_queue_returns_zero(stub_engine, stub_artefacts):
    """drain_once on an empty queue claims nothing and never calls the engine."""
    from src.commands.run_job_worker import drain_once

    reg = InMemoryRunRegistry()
    claimed = drain_once(reg)

    assert claimed == 0
    assert stub_engine["count"] == 0


def test_drain_once_engine_failure_marks_failed(monkeypatch, stub_artefacts):
    """An engine exception drives the record to failed, not an escaped error."""
    import src.services.validate_service as svc
    from src.commands.run_job_worker import drain_once

    def _boom(**kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(svc, "run_validate_service", _boom)

    reg = InMemoryRunRegistry()
    reg.put(_queued_record("job-fail"))

    claimed = drain_once(reg)

    assert claimed == 1
    rec = reg.get("job-fail")
    assert rec.status == "failed"
    assert "engine exploded" in (rec.error_message or "")


def test_async_on_live_worker_enqueues_fast(monkeypatch, stub_engine, tmp_path):
    """Async ON AND a live worker registered -> validate_file enqueues (no inline
    engine call); the worker then drains the queued job to completion."""
    import src.mcp.action_tools as at
    from src.commands.run_job_worker import drain_once

    # Fresh in-memory registry shared by the enqueue path AND the worker.
    reg = InMemoryRunRegistry()
    monkeypatch.setattr(at, "_get_registry", lambda: reg)

    # Async branch ON (now the default; pinned explicitly for clarity).
    monkeypatch.setenv("VALDO_MCP_ASYNC_VALIDATE", "1")
    # A worker has checked in recently -> the fast enqueue path is safe.
    reg.register_worker("worker-1", host="test")

    # Stub artefact resolution on action_tools so we need no real overlay.
    monkeypatch.setattr(
        at,
        "_resolve_artefacts",
        lambda source, file_path, file_type: {"mapping": None, "rules": None, "file_type": file_type},
    )
    # Worker re-resolves artefacts too.
    import src.commands.run_job_worker as worker
    monkeypatch.setattr(
        worker,
        "_resolve_artefacts",
        lambda source, file_path, file_type: {"mapping": None, "rules": None, "file_type": file_type},
    )

    data_file = tmp_path / "f.dat"
    data_file.write_text("row\n")

    start = time.monotonic()
    out = at.validate_file_payload(source="SHAW", file_path=str(data_file))
    elapsed = time.monotonic() - start

    # AC: enqueue returns fast (well under 100ms) and does NO engine work.
    assert elapsed < 0.1, f"validate_file took {elapsed*1000:.0f}ms — should just enqueue"
    assert stub_engine["count"] == 0, "async path must NOT run the engine inline"

    run_id = out["run_id"]
    # get_run_status reflects queued through the enqueue path.
    status = at.get_run_status_payload(run_id)
    assert status["status"] == "queued"

    # The worker drains it to completed; get_run_status now reflects done.
    assert drain_once(reg) == 1
    assert stub_engine["count"] == 1
    assert at.get_run_status_payload(run_id)["status"] == "completed"


def test_async_on_no_live_worker_runs_inline(monkeypatch, stub_engine, tmp_path):
    """Async ON but NO live worker -> validate_file falls back to a synchronous
    inline run so the validation always completes (no stranding)."""
    import src.mcp.action_tools as at

    reg = InMemoryRunRegistry()
    monkeypatch.setattr(at, "_get_registry", lambda: reg)

    # Async flag ON (the new default) but no worker has registered/heartbeated.
    monkeypatch.setenv("VALDO_MCP_ASYNC_VALIDATE", "1")
    assert reg.has_live_worker(within_seconds=60) is False

    monkeypatch.setattr(
        at,
        "_resolve_artefacts",
        lambda source, file_path, file_type: {"mapping": None, "rules": None, "file_type": file_type},
    )

    data_file = tmp_path / "f.dat"
    data_file.write_text("row\n")

    out = at.validate_file_payload(source="SHAW", file_path=str(data_file))

    # No worker draining -> the inline fallback ran the engine and the record
    # is already terminal on return rather than stranded in 'queued'.
    assert stub_engine["count"] == 1, "no-live-worker fallback must run inline"
    assert at.get_run_status_payload(out["run_id"])["status"] == "completed"


def test_async_off_always_inline(monkeypatch, stub_engine, tmp_path):
    """Async OFF -> validate_file runs the engine inline and the record is
    already terminal on return, regardless of worker presence (legacy path)."""
    import src.mcp.action_tools as at

    reg = InMemoryRunRegistry()
    monkeypatch.setattr(at, "_get_registry", lambda: reg)
    # Explicitly OFF (default is now ON, so we pin it).
    monkeypatch.setenv("VALDO_MCP_ASYNC_VALIDATE", "0")
    # Even with a live worker, async OFF must run inline.
    reg.register_worker("worker-1")
    monkeypatch.setattr(
        at,
        "_resolve_artefacts",
        lambda source, file_path, file_type: {"mapping": None, "rules": None, "file_type": file_type},
    )

    data_file = tmp_path / "f.dat"
    data_file.write_text("row\n")

    out = at.validate_file_payload(source="SHAW", file_path=str(data_file))

    assert stub_engine["count"] == 1, "async OFF must run the engine inline"
    assert at.get_run_status_payload(out["run_id"])["status"] == "completed"


def test_async_default_is_on(monkeypatch, stub_engine, tmp_path):
    """With the flag UNSET, the new default is ON: a live worker -> fast enqueue."""
    import src.mcp.action_tools as at

    reg = InMemoryRunRegistry()
    monkeypatch.setattr(at, "_get_registry", lambda: reg)
    # Flag unset entirely -> exercises the default.
    monkeypatch.delenv("VALDO_MCP_ASYNC_VALIDATE", raising=False)
    reg.register_worker("worker-1")
    monkeypatch.setattr(
        at,
        "_resolve_artefacts",
        lambda source, file_path, file_type: {"mapping": None, "rules": None, "file_type": file_type},
    )

    data_file = tmp_path / "f.dat"
    data_file.write_text("row\n")

    out = at.validate_file_payload(source="SHAW", file_path=str(data_file))

    # Default ON + live worker => enqueued, NOT run inline.
    assert stub_engine["count"] == 0, "unset flag should default to async ON"
    assert at.get_run_status_payload(out["run_id"])["status"] == "queued"


# ---------------------------------------------------------------------------
# Stuck-running reaper (S10-1, #397 — now implemented, delegates to registry)
# ---------------------------------------------------------------------------


def test_reap_stuck_running_delegates_to_registry(monkeypatch):
    """reap_stuck_running computes stale_before = now - threshold and delegates."""
    from datetime import datetime, timezone

    from src.commands.run_job_worker import reap_stuck_running

    captured = {}

    class _FakeReg:
        def reap_stuck(self, stale_before, now):
            captured["stale_before"] = stale_before
            captured["now"] = now
            return 3

    fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    count = reap_stuck_running(
        _FakeReg(), stuck_after_seconds=600, now_fn=lambda: fixed_now
    )

    assert count == 3
    assert captured["now"] == fixed_now
    # stale_before is 600s before now.
    assert (captured["now"] - captured["stale_before"]).total_seconds() == 600


def test_reap_stuck_running_reclaims_stale_leaves_fresh():
    """End-to-end against in-memory: stale running reclaimed, fresh left alone."""
    from datetime import datetime, timedelta, timezone

    from src.commands.run_job_worker import reap_stuck_running

    now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    def _iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    reg = InMemoryRunRegistry()
    reg.put(RunRecord(
        run_id="dead", source="S", file_path="/a", file_type=None,
        status="running", started_at=_iso(now - timedelta(hours=1)),
        last_heartbeat_at=_iso(now - timedelta(seconds=600)),
    ))
    reg.put(RunRecord(
        run_id="alive", source="S", file_path="/b", file_type=None,
        status="running", started_at=_iso(now - timedelta(hours=1)),
        last_heartbeat_at=_iso(now - timedelta(seconds=2)),
    ))

    reclaimed = reap_stuck_running(reg, stuck_after_seconds=300, now_fn=lambda: now)

    assert reclaimed == 1
    assert reg.get("dead").status == "queued"
    assert reg.get("alive").status == "running"


# ---------------------------------------------------------------------------
# During-run heartbeat thread: a long job is NOT falsely reaped (THE REFINEMENT)
# ---------------------------------------------------------------------------


def test_long_job_with_heartbeat_thread_is_not_reaped(stub_artefacts, monkeypatch):
    """A synchronous job longer than the reaper threshold survives, because the
    background heartbeat thread keeps last_heartbeat_at fresh; meanwhile a dead
    worker (no thread) with a stale heartbeat IS reaped."""
    import threading
    from datetime import datetime, timedelta, timezone

    import src.services.validate_service as svc
    from src.commands.run_job_worker import drain_once, reap_stuck_running

    reg = InMemoryRunRegistry()

    # Engine body that blocks until released — simulates a long validation.
    release = threading.Event()
    started = threading.Event()

    def _slow(**kwargs):
        started.set()
        release.wait(timeout=5)
        return {"valid": True, "errors": [], "warnings": [], "info": [], "total_rows": 1}

    monkeypatch.setattr(svc, "run_validate_service", _slow)

    # A dead-worker row: running, stale heartbeat, no live thread.
    def _iso(dt):
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

    base = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
    reg.put(RunRecord(
        run_id="dead", source="S", file_path="/dead", file_type=None,
        status="running", started_at=_iso(base - timedelta(hours=1)),
        last_heartbeat_at=_iso(base - timedelta(seconds=600)),
    ))

    # The live long-running job.
    reg.put(RunRecord(
        run_id="live", source="SHAW", file_path="/tmp/a.dat", file_type=None,
        status="queued", started_at=_iso(base),
    ))

    # Run drain_once in a background thread with a fast heartbeat interval so
    # the during-run heartbeat thread stamps last_heartbeat_at while _slow blocks.
    worker = threading.Thread(
        target=drain_once, kwargs={"registry": reg, "heartbeat_interval": 0.05}
    )
    worker.start()
    assert started.wait(timeout=5), "engine body never started"

    # Let at least one heartbeat fire while the job is mid-flight.
    time.sleep(0.2)

    # Now run the reaper with a threshold that WOULD reclaim a row whose
    # heartbeat is older than 0.1s. The dead row (stale) must be reaped; the
    # live row (fresh heartbeat from the thread) must survive.
    now = datetime.now(timezone.utc)
    reclaimed = reap_stuck_running(reg, stuck_after_seconds=0.1, now_fn=lambda: now)

    assert reg.get("live").status == "running", "live job falsely reaped despite heartbeat"
    assert reg.get("dead").status == "queued", "dead worker row should be reaped"
    assert reclaimed == 1

    # Let the job finish cleanly and the heartbeat thread stop.
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert reg.get("live").status == "completed"


# ---------------------------------------------------------------------------
# Poll loop: fake clock + fake sleep
# ---------------------------------------------------------------------------


def test_poll_loop_drains_then_exits_on_max_runs(stub_engine, stub_artefacts):
    """run_worker_loop drains N jobs then stops at --max-runs."""
    from src.commands.run_job_worker import run_worker_loop

    reg = InMemoryRunRegistry()
    for i in range(3):
        reg.put(_queued_record(f"job-{i}", file_path=f"/tmp/{i}.dat"))

    sleeps = []

    class _Shutdown:
        def is_set(self):
            return False

    drained = run_worker_loop(
        reg,
        poll_interval=1.0,
        max_runs=2,
        reap_multiple=10,
        sleep_fn=sleeps.append,
        shutdown=_Shutdown(),
    )

    assert drained == 2
    # Two jobs drained back-to-back -> no idle sleep needed.
    assert sleeps == []
    # One job left queued.
    queued = [r for r in [reg.get(f"job-{i}") for i in range(3)] if r.status == "queued"]
    assert len(queued) == 1


def test_poll_loop_empty_queue_sleeps_then_stops(stub_engine, stub_artefacts, monkeypatch):
    """An empty queue runs the reaper, sleeps with backoff, and we can stop it."""
    import src.commands.run_job_worker as worker_mod
    from src.commands.run_job_worker import run_worker_loop

    reg = InMemoryRunRegistry()  # empty -> always idle

    sleeps = []

    # Stop after a few idle cycles by flipping the shutdown flag.
    class _Shutdown:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            # First check (top of loop) returns False a few times, then True.
            return self.calls > 3

    # Capture reaper invocations.
    reaped = {"count": 0}
    monkeypatch.setattr(
        worker_mod, "reap_stuck_running",
        lambda registry, stuck_after_seconds: reaped.__setitem__("count", reaped["count"] + 1) or 0,
    )

    drained = run_worker_loop(
        reg,
        poll_interval=2.0,
        max_runs=0,
        reap_multiple=10,
        sleep_fn=sleeps.append,
        shutdown=_Shutdown(),
    )

    assert drained == 0
    # Idle cycles slept at least once and the reaper ran on idle.
    assert len(sleeps) >= 1
    assert reaped["count"] >= 1
    # Backoff grows: each idle sleep is >= the previous (capped).
    assert all(b >= a for a, b in zip(sleeps, sleeps[1:]))


def test_poll_loop_graceful_shutdown_before_claiming(stub_engine, stub_artefacts):
    """Shutdown set before the first claim -> loop exits 0 without draining."""
    from src.commands.run_job_worker import run_worker_loop

    reg = InMemoryRunRegistry()
    reg.put(_queued_record("untouched"))

    class _Shutdown:
        def is_set(self):
            return True  # already shutting down

    drained = run_worker_loop(
        reg,
        poll_interval=1.0,
        max_runs=0,
        reap_multiple=10,
        sleep_fn=lambda s: None,
        shutdown=_Shutdown(),
    )

    assert drained == 0
    assert stub_engine["count"] == 0
    # The queued row persists for the next worker.
    assert reg.get("untouched").status == "queued"
