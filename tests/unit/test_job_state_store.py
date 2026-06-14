from datetime import datetime, timezone

from src.contracts.task_contracts import TaskRequest, TaskResult
from src.services.job_state_store import JobStateStore


def test_job_state_store_crud(tmp_path):
    store = JobStateStore(db_path=tmp_path / "jobs.db")

    req = TaskRequest(
        task_id="task-100",
        trace_id="trace-100",
        idempotency_key="idem-100",
        source="cli",
        intent="validate",
        payload={"x": 1},
        priority="normal",
        deadline=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    result = TaskResult(task_id="task-100", trace_id="trace-100", status="queued", result={"ok": True})

    store.create(req, result)
    row = store.get("task-100")
    assert row is not None
    assert row["status"] == "queued"

    updated = TaskResult(task_id="task-100", trace_id="trace-100", status="dead-letter", result={"ok": False})
    assert store.update_status("task-100", "dead-letter", updated) is True

    row2 = store.get("task-100")
    assert row2 is not None
    assert row2["status"] == "dead-letter"

    items = store.list(limit=10)
    assert len(items) >= 1
    assert items[0]["task_id"] == "task-100"
