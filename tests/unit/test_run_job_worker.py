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
5. The stuck-``running`` reaper is a documented stub this sprint and must
   raise ``NotImplementedError`` (the implementation is the fast-follow).

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


def test_async_flag_enqueues_only_then_worker_drains(monkeypatch, stub_engine, tmp_path):
    """With the async flag ON, validate_file enqueues (no inline engine call);
    the worker then drains the queued job to completion."""
    import src.mcp.action_tools as at
    from src.commands.run_job_worker import drain_once

    # Fresh in-memory registry shared by the enqueue path AND the worker.
    reg = InMemoryRunRegistry()
    monkeypatch.setattr(at, "_get_registry", lambda: reg)

    # Async branch ON for this test (sprint default is OFF).
    monkeypatch.setenv("VALDO_MCP_ASYNC_VALIDATE", "1")

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


def test_sync_flag_default_runs_inline(monkeypatch, stub_engine, tmp_path):
    """With the flag OFF (sprint default), validate_file runs the engine inline
    and the record is already terminal on return (legacy behaviour)."""
    import src.mcp.action_tools as at

    reg = InMemoryRunRegistry()
    monkeypatch.setattr(at, "_get_registry", lambda: reg)
    monkeypatch.delenv("VALDO_MCP_ASYNC_VALIDATE", raising=False)
    monkeypatch.setattr(
        at,
        "_resolve_artefacts",
        lambda source, file_path, file_type: {"mapping": None, "rules": None, "file_type": file_type},
    )

    data_file = tmp_path / "f.dat"
    data_file.write_text("row\n")

    out = at.validate_file_payload(source="SHAW", file_path=str(data_file))

    assert stub_engine["count"] == 1, "sync default must run the engine inline"
    assert at.get_run_status_payload(out["run_id"])["status"] == "completed"


def test_reaper_is_a_documented_stub():
    """The stuck-running reaper is intentionally unimplemented this sprint.

    ADR 0021 defines the contract but defers the implementation to the
    fast-follow (M). The skeleton ships the hook raising NotImplementedError
    so a premature call fails loudly rather than silently no-op'ing.
    """
    from src.commands.run_job_worker import reap_stuck_running

    with pytest.raises(NotImplementedError):
        reap_stuck_running(InMemoryRunRegistry(), stuck_after_seconds=3600)
